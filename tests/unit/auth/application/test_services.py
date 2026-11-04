"""API-key service tests."""

from dataclasses import replace
from functools import partial
from typing import Literal

import pytest
from whenever import Instant

from squid.auth.application.services import LAST_USED_WRITE_INTERVAL_SECONDS, ApiKeyService, hash_api_key_secret
from squid.auth.models.entities import ApiKey
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import PermissionStore, SubjectRecords
from squid.permissions.domain import InvalidPatternError, Pattern
from squid.permissions.models import PermissionGrant
from tests.support.permissions import permission_uow_factory
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork

NOW = Instant.from_utc(2026, 8, 8, 12)


def nodes(*raw: str) -> frozenset[Pattern]:
    return frozenset(Pattern.parse(pattern) for pattern in raw)


class FakeApiKeyRepository:
    """Store keys and record usage requests without updating last-used timestamps."""

    def __init__(self) -> None:
        self.keys: dict[str, ApiKey] = {}
        self.lookups: list[str] = []
        self.touches: list[tuple[str, Instant, str | None, Instant]] = []

    async def add(
        self,
        *,
        key_id: str,
        secret_hash: bytes,
        label: str,
        scopes: frozenset[Pattern],
        owner_account_id: int | None,
        created_by_account_id: int | None,
        expires_at: Instant | None,
    ) -> ApiKey:
        key = ApiKey(
            id=len(self.keys) + 1,
            key_id=key_id,
            secret_hash=secret_hash,
            label=label,
            scopes=scopes,
            owner_account_id=owner_account_id,
            created_by_account_id=created_by_account_id,
            created_at=NOW,
            expires_at=expires_at,
        )
        self.keys[key_id] = key
        return key

    async def get_by_key_id(self, key_id: str) -> ApiKey | None:
        self.lookups.append(key_id)
        return self.keys.get(key_id)

    async def touch_last_used(
        self,
        key_id: str,
        *,
        used_at: Instant,
        used_ip: str | None,
        older_than: Instant,
    ) -> None:
        self.touches.append((key_id, used_at, used_ip, older_than))


class FakeApiKeyUnitOfWork(FakeUnitOfWork[FakeApiKeyRepository]):
    def __init__(
        self, committed: CommittedState[FakeApiKeyRepository], permissions: PermissionStore | None = None
    ) -> None:
        super().__init__(committed)
        self._permissions = permissions

    @property
    def api_keys(self) -> FakeApiKeyRepository:
        return self.state

    @property
    def permissions(self) -> PermissionStore:
        assert self._permissions is not None
        return self._permissions


def _copy_repository(repository: FakeApiKeyRepository) -> FakeApiKeyRepository:
    copied = FakeApiKeyRepository()
    copied.keys = {key: replace(value) for key, value in repository.keys.items()}
    # Keep attempted reads observable even when the transaction is not committed.
    copied.lookups = repository.lookups
    copied.touches = list(repository.touches)
    return copied


def _state() -> CommittedState[FakeApiKeyRepository]:
    return CommittedState(FakeApiKeyRepository(), clone=_copy_repository)


def service(repository: CommittedState[FakeApiKeyRepository]) -> ApiKeyService:
    return ApiKeyService(
        "test-api-key-pepper",
        uow_factory=partial(FakeApiKeyUnitOfWork, repository),
        now=lambda: NOW,
        token_bytes=lambda size: b"x" * size,
    )


@pytest.mark.asyncio
async def test_issue_returns_secret_once_and_stores_only_its_hmac() -> None:
    repository = _state()

    issued = await service(repository).issue(
        label="Minecraft server",
        scopes={"account.verify.relay", "build.submission.create"},
        owner_account_id=4,
        created_by_account_id=7,
    )

    assert issued.token.startswith(f"sq_{issued.key.key_id}_")
    secret = issued.token.split("_", 2)[2]
    assert issued.key.secret_hash == service(repository).hash_secret(secret)
    assert secret.encode() not in issued.key.secret_hash
    assert issued.key.scopes == nodes("account.verify.relay", "build.submission.create")
    assert issued.key.owner_account_id == 4
    assert issued.key.created_by_account_id == 7


@pytest.mark.asyncio
async def test_authenticate_returns_active_key_and_records_throttled_use() -> None:
    repository = _state()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"account.verify.relay"})

    authenticated = await api_keys.authenticate(issued.token, used_ip="192.0.2.4")

    assert authenticated is not None
    assert authenticated.id == issued.key.id
    assert repository.value.touches == [
        (
            issued.key.key_id,
            NOW,
            "192.0.2.4",
            NOW.subtract(seconds=LAST_USED_WRITE_INTERVAL_SECONDS),
        )
    ]


@pytest.mark.parametrize("entropy", [b"\xff", b"\xfb", b"x"])
async def test_issued_keys_round_trip_with_base64url_separators(entropy: bytes) -> None:
    repository = _state()
    api_keys = ApiKeyService(
        "test-api-key-pepper",
        uow_factory=partial(FakeApiKeyUnitOfWork, repository),
        now=lambda: NOW,
        token_bytes=lambda size: entropy * size,
    )
    issued = await api_keys.issue(label="Round trip", scopes={"account.verify.relay"})

    authenticated = await api_keys.authenticate(issued.token)
    assert authenticated is not None
    assert authenticated.id == issued.key.id
    assert repository.value.lookups == [issued.key.key_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-key",
        "sq__secret",
        "sq_key_",
        "sq_123456789012345_secret",
        "sq_12345678901234567_secret",
        "sq_1234567890123456-secret",
        "sq_1234567890123456_",
        "other_1234567890123456_secret",
    ],
)
async def test_malformed_tokens_do_not_reach_persistence(token: str) -> None:
    repository = _state()

    assert await service(repository).authenticate(token) is None
    assert repository.value.lookups == []


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected_without_recording_use() -> None:
    repository = _state()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"account.verify.relay"})

    assert await api_keys.authenticate(f"sq_{issued.key.key_id}_wrong") is None
    assert repository.value.touches == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revoked_at", NOW.subtract(seconds=1)),
        ("expires_at", NOW),
        ("expires_at", NOW.subtract(seconds=1)),
    ],
)
async def test_inactive_keys_are_rejected(field: Literal["expires_at", "revoked_at"], value: Instant) -> None:
    repository = _state()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"account.verify.relay"})
    if field == "revoked_at":
        repository.value.keys[issued.key.key_id] = replace(issued.key, revoked_at=value)
    else:
        repository.value.keys[issued.key.key_id] = replace(issued.key, expires_at=value)

    assert await api_keys.authenticate(issued.token) is None
    assert repository.value.touches == []


@pytest.mark.asyncio
async def test_unexpired_key_remains_active() -> None:
    repository = _state()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"account.verify.relay"}, expires_at=NOW.add(seconds=1))

    assert await api_keys.authenticate(issued.token) is not None


def test_empty_pepper_is_rejected() -> None:
    with pytest.raises(InvalidStateError, match="must not be empty"):
        ApiKeyService("", uow_factory=partial(FakeApiKeyUnitOfWork, _state()))


class TestDigestConstruction:
    """The stored digest is HMAC-SHA-256 keyed by the deployment pepper.

    See `docs/credential-hashing.md` for why that is the right primitive for a
    256-bit random secret, and why a password KDF is not.
    """

    def test_a_known_answer_pins_the_construction(self) -> None:
        """Changing the primitive, key order, or encoding would invalidate stored digests."""
        expected = bytes.fromhex("e85a71116346fb122fbf70bda5503f9d4afd28fd940c40b651ed37784439ed7b")

        assert service(_state()).hash_secret("a-known-secret") == expected
        assert hash_api_key_secret(b"test-api-key-pepper", "a-known-secret") == expected

    @pytest.mark.asyncio
    async def test_a_rotated_pepper_stops_authenticating_old_tokens(self) -> None:
        """A different pepper cannot authenticate a digest created with the old key."""
        repository = _state()
        issued = await service(repository).issue(label="CI", scopes={"account.verify.relay"})
        rotated = ApiKeyService(
            "a-different-pepper", uow_factory=partial(FakeApiKeyUnitOfWork, repository), now=lambda: NOW
        )

        assert await rotated.authenticate(issued.token) is None
        assert repository.value.touches == []


class TestScopeValidation:
    """Scopes are parsed patterns, not free strings, from issuance onward."""

    @pytest.mark.asyncio
    async def test_a_malformed_pattern_is_rejected_without_a_permission_service(self) -> None:
        """Scope syntax must be checked even when owner-authority validation is disabled."""
        repository = _state()

        with pytest.raises(InvalidPatternError):
            await service(repository).issue(label="CI", scopes={"buildsubmission.raed."})

        assert repository.value.keys == {}

    @pytest.mark.asyncio
    async def test_equivalent_patterns_are_stored_once(self) -> None:
        repository = _state()

        issued = await service(repository).issue(label="CI", scopes=["build.**", " build.** ", "build.**"])

        assert issued.key.scopes == nodes("build.**")


class TestIssuanceBoundary:
    """Owner-authority checks for issuance, with an explicit ownerless-key case."""

    @staticmethod
    def _authorized_service(repository: CommittedState[FakeApiKeyRepository], *held: str) -> ApiKeyService:
        class Store:
            async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
                return SubjectRecords(
                    epoch=1,
                    grants=tuple(PermissionGrant(pattern=node, effect=1, subject_account_id=4) for node in held),
                )

            async def epoch(self) -> int:
                return 1

        store = Store()
        return ApiKeyService(
            "test-api-key-pepper",
            uow_factory=partial(FakeApiKeyUnitOfWork, repository, store),
            permissions=PermissionService(uow_factory=permission_uow_factory(store)),
        )

    async def test_a_pattern_the_owner_holds_is_issued(self) -> None:
        repository = _state()
        api_keys = self._authorized_service(repository, "build.**")

        issued = await api_keys.issue(label="CI", scopes={"build.submission.create"}, owner_account_id=4)

        assert issued.key.scopes == nodes("build.submission.create")

    async def test_a_pattern_beyond_the_owner_is_refused(self) -> None:
        """Reject excess authority at issuance even if the owner might gain that permission later."""
        repository = _state()
        api_keys = self._authorized_service(repository, "build.**")

        with pytest.raises(AuthorizationError):
            await api_keys.issue(label="CI", scopes={"bot.tree.sync"}, owner_account_id=4)

        assert repository.value.keys == {}

    async def test_an_ownerless_key_is_bounded_only_by_its_own_nodes(self) -> None:
        repository = _state()
        api_keys = self._authorized_service(repository)

        issued = await api_keys.issue(label="CI", scopes={"bot.tree.sync"})

        assert issued.key.scopes == nodes("bot.tree.sync")
