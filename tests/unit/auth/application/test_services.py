"""API-key service tests."""

from dataclasses import replace
from typing import Literal

import pytest
from whenever import Instant

from squid.auth.application.services import LAST_USED_WRITE_INTERVAL_SECONDS, ApiKeyService
from squid.auth.domain import ApiKey

NOW = Instant.from_utc(2026, 8, 8, 12)


class FakeApiKeyRepository:
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
        scopes: frozenset[str],
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


def service(repository: FakeApiKeyRepository) -> ApiKeyService:
    return ApiKeyService(repository, "test-api-key-pepper", now=lambda: NOW, token_bytes=lambda size: b"x" * size)


@pytest.mark.asyncio
async def test_issue_returns_secret_once_and_stores_only_its_hmac() -> None:
    repository = FakeApiKeyRepository()

    issued = await service(repository).issue(
        label="Minecraft server",
        scopes={"verify", "builds:write"},
        owner_account_id=4,
        created_by_account_id=7,
    )

    assert issued.token.startswith(f"sq_{issued.key.key_id}_")
    secret = issued.token.split("_", 2)[2]
    assert issued.key.secret_hash == service(repository).hash_secret(secret)
    assert secret.encode() not in issued.key.secret_hash
    assert issued.key.scopes == frozenset({"verify", "builds:write"})
    assert issued.key.owner_account_id == 4
    assert issued.key.created_by_account_id == 7


@pytest.mark.asyncio
async def test_authenticate_returns_active_key_and_records_throttled_use() -> None:
    repository = FakeApiKeyRepository()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"verify"})

    authenticated = await api_keys.authenticate(issued.token, used_ip="192.0.2.4")

    assert authenticated == issued.key
    assert repository.touches == [
        (
            issued.key.key_id,
            NOW,
            "192.0.2.4",
            NOW.subtract(seconds=LAST_USED_WRITE_INTERVAL_SECONDS),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "not-a-key", "sq__secret", "sq_key_"])
async def test_malformed_tokens_do_not_reach_persistence(token: str) -> None:
    repository = FakeApiKeyRepository()

    assert await service(repository).authenticate(token) is None
    assert repository.lookups == []


@pytest.mark.asyncio
async def test_wrong_secret_is_rejected_without_recording_use() -> None:
    repository = FakeApiKeyRepository()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"verify"})

    assert await api_keys.authenticate(f"sq_{issued.key.key_id}_wrong") is None
    assert repository.touches == []


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
    repository = FakeApiKeyRepository()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"verify"})
    if field == "revoked_at":
        repository.keys[issued.key.key_id] = replace(issued.key, revoked_at=value)
    else:
        repository.keys[issued.key.key_id] = replace(issued.key, expires_at=value)

    assert await api_keys.authenticate(issued.token) is None
    assert repository.touches == []


@pytest.mark.asyncio
async def test_unexpired_key_remains_active() -> None:
    repository = FakeApiKeyRepository()
    api_keys = service(repository)
    issued = await api_keys.issue(label="CI", scopes={"verify"}, expires_at=NOW.add(seconds=1))

    assert await api_keys.authenticate(issued.token) is not None


def test_empty_pepper_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ApiKeyService(FakeApiKeyRepository(), "")
