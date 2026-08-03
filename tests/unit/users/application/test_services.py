"""User application service tests."""

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

import pytest
from whenever import Instant

from squid.users.application import UserService
from squid.users.domain import (
    CURRENT_CONSENT_VERSION,
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    UserAccount,
    UserConsent,
    VerificationCode,
    normalize_ign,
)
from squid.users.errors import (
    AccountAlreadyLinkedError,
    AliasAlreadyClaimedError,
    ClaimNotFoundError,
    ConsentRequiredError,
    CreatorAliasNotFoundError,
    InvalidVerificationCodeError,
    MinecraftAccountNotFoundError,
    UserNotFoundError,
)

EXISTING_MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")
CONSENT = UserConsent(CURRENT_CONSENT_VERSION, Instant.from_utc(2026, 8, 4))
STALE_CONSENT = UserConsent("2026-08-03", Instant.from_utc(2026, 8, 3))


class FakeUserRepository:
    def __init__(self) -> None:
        self.user: UserAccount | None = None
        self.code: VerificationCode | None = None
        self.created_code: str | None = None
        self.aliases: dict[int, CreatorAlias] = {}
        self.claims: dict[int, AliasClaim] = {}
        self._next_id = 1

    def add_alias(self, name: str, *, user_id: int | None = None) -> CreatorAlias:
        """Seed a creator credit, as a build submission would."""
        alias = CreatorAlias(
            id=self._take_id(),
            name=name,
            user_id=user_id,
            claimed_at=Instant.now() if user_id is not None else None,
            claim_method=ClaimMethod.MIGRATED if user_id is not None else None,
        )
        self.aliases[alias.id] = alias
        return alias

    def _take_id(self) -> int:
        taken = self._next_id
        self._next_id += 1
        return taken

    def _find_alias(self, name: str) -> CreatorAlias | None:
        normalized = normalize_ign(name)
        return next((alias for alias in self.aliases.values() if normalize_ign(alias.name) == normalized), None)

    async def add(
        self,
        *,
        consent: UserConsent,
        discord_id: int | None = None,
        minecraft_uuid: UUID | None = None,
        ign: str | None = None,
    ) -> UserAccount:
        self.user = UserAccount(discord_id, minecraft_uuid, ign, consent, id=self._take_id())
        return self.user

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None:
        return self.user if self.user is not None and self.user.discord_id == discord_id else None

    async def update(self, user: UserAccount) -> None:
        self.user = user

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        if self.user is None or self.user.discord_id != discord_id:
            return False
        self.user.minecraft_uuid = None
        return True

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        return self._find_alias(name)

    async def claim_unclaimed_alias(self, *, user_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        alias = self._find_alias(name)
        if alias is None or alias.user_id is not None:
            return None
        claimed = CreatorAlias(
            id=alias.id, name=alias.name, user_id=user_id, claimed_at=Instant.now(), claim_method=method
        )
        self.aliases[alias.id] = claimed
        return claimed

    async def request_claim(self, *, name: str, user_id: int) -> AliasClaim:
        alias = self._find_alias(name)
        if alias is None:
            raise CreatorAliasNotFoundError(name)
        if alias.user_id is not None:
            raise AliasAlreadyClaimedError(alias.name)
        claim = AliasClaim(
            id=self._take_id(),
            alias_id=alias.id,
            alias_name=alias.name,
            user_id=user_id,
            status=ClaimStatus.PENDING,
            created_at=Instant.now(),
        )
        self.claims[claim.id] = claim
        return claim

    async def get_claim(self, claim_id: int) -> AliasClaim | None:
        return self.claims.get(claim_id)

    async def pending_claims(self) -> Sequence[AliasClaim]:
        return [claim for claim in self.claims.values() if claim.status is ClaimStatus.PENDING]

    async def resolve_claim(self, *, claim_id: int, status: ClaimStatus, resolved_by_discord_id: int) -> AliasClaim:
        claim = self.claims[claim_id]
        alias = self.aliases[claim.alias_id]
        if status is ClaimStatus.APPROVED:
            if alias.user_id is not None:
                raise AliasAlreadyClaimedError(alias.name)
            self.aliases[alias.id] = CreatorAlias(
                id=alias.id,
                name=alias.name,
                user_id=claim.user_id,
                claimed_at=Instant.now(),
                claim_method=ClaimMethod.STAFF_APPROVED,
            )
        resolved = AliasClaim(
            id=claim.id,
            alias_id=claim.alias_id,
            alias_name=claim.alias_name,
            user_id=claim.user_id,
            status=status,
            created_at=claim.created_at,
            resolved_at=Instant.now(),
            resolved_by_discord_id=resolved_by_discord_id,
        )
        self.claims[claim_id] = resolved
        return resolved

    async def get_valid_verification_code(self, code: str) -> VerificationCode | None:
        return self.code

    async def invalidate_codes(self, minecraft_uuid: UUID) -> None:
        return None

    async def create_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None:
        self.created_code = code


def username_lookup(username: str | None) -> Callable[[UUID], Awaitable[str | None]]:
    async def lookup(_minecraft_uuid: UUID) -> str | None:
        return username

    return lookup


def linked_service(repository: FakeUserRepository, username: str = "Player") -> UserService:
    """Build a service whose verification code resolves to *username*."""
    repository.code = VerificationCode(EXISTING_MINECRAFT_UUID, username)
    return UserService(repository, username_lookup(username), lambda: 123456)


async def test_user_link_rejects_invalid_code() -> None:
    service = UserService(FakeUserRepository(), username_lookup("Player"), lambda: 123456)

    with pytest.raises(InvalidVerificationCodeError, match="invalid or expired"):
        await service.link_minecraft_account(1, "bad", consent=CONSENT)


async def test_user_link_and_code_generation() -> None:
    repository = FakeUserRepository()
    minecraft_uuid = EXISTING_MINECRAFT_UUID
    repository.code = VerificationCode(minecraft_uuid, "Player")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    await service.link_minecraft_account(1, "valid", consent=CONSENT)
    generated = await service.generate_verification_code(minecraft_uuid)

    assert repository.user == UserAccount(1, minecraft_uuid, "Player", CONSENT, id=1)
    assert generated == 123456
    assert repository.created_code == "123456"


async def test_user_link_rejects_a_different_existing_account() -> None:
    repository = FakeUserRepository()
    existing_uuid = EXISTING_MINECRAFT_UUID
    requested_uuid = UUID("22222222-2222-2222-2222-222222222222")
    repository.user = UserAccount(1, existing_uuid, "Existing")
    repository.code = VerificationCode(requested_uuid, "Requested")
    service = UserService(repository, username_lookup("Requested"), lambda: 123456)

    with pytest.raises(AccountAlreadyLinkedError) as exc_info:
        await service.link_minecraft_account(1, "valid", consent=CONSENT)

    assert exc_info.value.context == {"discord_id": 1, "minecraft_uuid": str(existing_uuid)}
    assert exc_info.value.public_context == {}


async def test_code_generation_rejects_unknown_minecraft_account() -> None:
    minecraft_uuid = EXISTING_MINECRAFT_UUID
    service = UserService(FakeUserRepository(), username_lookup(None), lambda: 123456)

    with pytest.raises(MinecraftAccountNotFoundError) as exc_info:
        await service.generate_verification_code(minecraft_uuid)

    assert exc_info.value.public_context == {"minecraft_uuid": str(minecraft_uuid)}


async def test_linking_claims_a_matching_credit_ignoring_case() -> None:
    """The whole point of the split: an inferred credit reaches its owner."""
    repository = FakeUserRepository()
    repository.add_alias("player")
    service = linked_service(repository, "Player")

    claimed = await service.link_minecraft_account(1, "valid", consent=CONSENT)

    assert claimed is not None
    assert claimed.name == "player"
    assert claimed.claim_method is ClaimMethod.VERIFIED_IGN
    assert repository.user is not None
    assert repository.aliases[claimed.id].user_id == repository.user.id


async def test_linking_leaves_a_credit_someone_else_holds() -> None:
    repository = FakeUserRepository()
    alias = repository.add_alias("Player", user_id=99)
    service = linked_service(repository, "Player")

    claimed = await service.link_minecraft_account(1, "valid", consent=CONSENT)

    assert claimed is None
    assert repository.aliases[alias.id].user_id == 99


async def test_linking_without_a_matching_credit_claims_nothing() -> None:
    repository = FakeUserRepository()
    repository.add_alias("SomeoneElse")
    service = linked_service(repository, "Player")

    assert await service.link_minecraft_account(1, "valid", consent=CONSENT) is None


async def test_claim_request_requires_a_known_creator_name() -> None:
    repository = FakeUserRepository()
    repository.user = UserAccount(1, EXISTING_MINECRAFT_UUID, "Player", CONSENT, id=1)
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    with pytest.raises(CreatorAliasNotFoundError):
        await service.request_alias_claim(1, "NeverCredited")


async def test_claim_request_requires_a_linked_account() -> None:
    service = UserService(FakeUserRepository(), username_lookup("Player"), lambda: 123456)

    with pytest.raises(UserNotFoundError):
        await service.request_alias_claim(1, "OldName")


async def test_claim_request_requires_the_current_consent_version() -> None:
    repository = FakeUserRepository()
    repository.user = UserAccount(1, EXISTING_MINECRAFT_UUID, "Player", STALE_CONSENT, id=1)
    repository.add_alias("OldName")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    with pytest.raises(ConsentRequiredError):
        await service.request_alias_claim(1, "OldName")


async def test_approving_a_claim_credits_the_claimant() -> None:
    repository = FakeUserRepository()
    repository.user = UserAccount(1, EXISTING_MINECRAFT_UUID, "Player", CONSENT, id=1)
    alias = repository.add_alias("OldName")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    claim = await service.request_alias_claim(1, "oldname")
    assert [pending.id for pending in await service.pending_alias_claims()] == [claim.id]

    resolved = await service.approve_alias_claim(claim.id, staff_discord_id=7)

    assert resolved.status is ClaimStatus.APPROVED
    assert resolved.resolved_by_discord_id == 7
    assert repository.aliases[alias.id].user_id == 1
    assert repository.aliases[alias.id].claim_method is ClaimMethod.STAFF_APPROVED
    assert await service.pending_alias_claims() == []


async def test_rejecting_a_claim_leaves_the_credit_unclaimed() -> None:
    repository = FakeUserRepository()
    repository.user = UserAccount(1, EXISTING_MINECRAFT_UUID, "Player", CONSENT, id=1)
    alias = repository.add_alias("OldName")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    claim = await service.request_alias_claim(1, "OldName")
    resolved = await service.reject_alias_claim(claim.id, staff_discord_id=7)

    assert resolved.status is ClaimStatus.REJECTED
    assert repository.aliases[alias.id].user_id is None


async def test_resolving_an_already_resolved_claim_is_rejected() -> None:
    repository = FakeUserRepository()
    repository.user = UserAccount(1, EXISTING_MINECRAFT_UUID, "Player", CONSENT, id=1)
    repository.add_alias("OldName")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    claim = await service.request_alias_claim(1, "OldName")
    await service.approve_alias_claim(claim.id, staff_discord_id=7)

    with pytest.raises(ClaimNotFoundError):
        await service.approve_alias_claim(claim.id, staff_discord_id=7)
