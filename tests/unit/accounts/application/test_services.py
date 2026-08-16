"""Account application service tests."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.application.ports import VerificationLinkResult
from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    Account,
    AccountConsent,
    AccountIdentity,
    AccountMerge,
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    IdentityProvider,
    IdentityRefresh,
    RecentAccountProof,
    fold_creator_name,
)
from squid.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountNotFoundError,
    ClaimNotFoundError,
    ConsentRequiredError,
    InvalidMergeProofError,
    InvalidVerificationCodeError,
    MinecraftAccountNotFoundError,
    NoLinkedMinecraftAccountError,
)

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_JAVA_UUID = UUID("22222222-2222-2222-2222-222222222222")
NOW = Instant.from_utc(2026, 8, 11, 12)
CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, NOW)


class FakeAccountRepository:
    """Narrow stateful fake used to exercise account orchestration."""

    def __init__(self) -> None:
        self.accounts: dict[int, Account] = {}
        self.aliases: dict[int, CreatorAlias] = {}
        self.claims: dict[int, AliasClaim] = {}
        self.link_result = VerificationLinkResult()
        self.created_code: tuple[UUID, str, str] | None = None
        self.merge_result: AccountMerge | None = None
        self.refreshed: tuple[int, UUID, str] | None = None
        self._next_id = 1

    def seed_account(
        self,
        discord_id: int,
        *,
        consent: AccountConsent | None = None,
        java_uuid: UUID | None = None,
    ) -> Account:
        identities = [AccountIdentity.discord(discord_id, verified_at=NOW)]
        if java_uuid is not None:
            identities.append(AccountIdentity.java(java_uuid, username="Player", verified_at=NOW))
        account = Account(tuple(identities), consent, self._next_id, NOW, UUID(int=self._next_id))
        self.accounts[self._next_id] = account
        self._next_id += 1
        return account

    async def create(
        self,
        *,
        consent: AccountConsent | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account:
        account = Account(tuple(identities), consent, self._next_id, NOW, UUID(int=self._next_id))
        self.accounts[self._next_id] = account
        self._next_id += 1
        return account

    async def get_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return next(
            (
                account
                for account in self.accounts.values()
                if (identity := account.identity(provider)) is not None and identity.subject == subject
            ),
            None,
        )

    async def unlink_java_identity(self, discord_id: int) -> bool:
        entry = next(
            (
                (account_id, account)
                for account_id, account in self.accounts.items()
                if (identity := account.identity(IdentityProvider.DISCORD)) is not None
                and identity.subject == str(discord_id)
            ),
            None,
        )
        if entry is None:
            return False
        account_id, account = entry
        if account.identity(IdentityProvider.JAVA) is None:
            return False
        remaining = tuple(identity for identity in account.identities if identity.provider is not IdentityProvider.JAVA)
        self.accounts[account_id] = Account(
            remaining, account.consent, account.id, account.created_at, account.public_creator_id
        )
        return True

    async def claim_unclaimed_alias(self, *, account_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        alias = await self.get_alias_by_name(name)
        if alias is None or alias.account_id is not None:
            return None
        claimed = replace(alias, account_id=account_id, claimed_at=NOW, claim_method=method)
        self.aliases[alias.id] = claimed
        return claimed

    async def get_by_discord_id(self, discord_id: int) -> Account | None:
        return next(
            (
                account
                for account in self.accounts.values()
                if (identity := account.identity(IdentityProvider.DISCORD)) is not None
                and identity.subject == str(discord_id)
            ),
            None,
        )

    async def get_by_id(self, account_id: int) -> Account | None:
        return self.accounts.get(account_id)

    async def get_or_create_discord(self, discord_id: int) -> Account:
        return await self.get_by_discord_id(discord_id) or self.seed_account(discord_id)

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account:
        account = self.accounts[account_id]
        updated = Account(account.identities, consent, account.id, account.created_at, account.public_creator_id)
        self.accounts[account_id] = updated
        return updated

    async def consume_code_and_link_account(
        self, *, discord_id: int, code: str, consent: AccountConsent
    ) -> VerificationLinkResult:
        return self.link_result

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge:
        assert self.merge_result is not None
        return self.merge_result

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        return next(
            (alias for alias in self.aliases.values() if fold_creator_name(alias.name) == fold_creator_name(name)),
            None,
        )

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        return CreatorProfile(public_id, tuple(alias.name for alias in self.aliases.values()))

    async def request_claim(self, *, name: str, account_id: int) -> AliasClaim:
        alias = await self.get_alias_by_name(name)
        assert alias is not None
        claim = AliasClaim(len(self.claims) + 1, alias.id, alias.name, account_id, ClaimStatus.PENDING, NOW)
        self.claims[claim.id] = claim
        return claim

    async def get_claim(self, claim_id: int) -> AliasClaim | None:
        return self.claims.get(claim_id)

    async def pending_claims(self) -> Sequence[AliasClaim]:
        return tuple(claim for claim in self.claims.values() if claim.status is ClaimStatus.PENDING)

    async def refresh_java_identity(self, *, account_id: int, java_uuid: UUID, username: str) -> IdentityRefresh:
        self.refreshed = (account_id, java_uuid, username)
        return IdentityRefresh(
            account_id=account_id,
            java_uuid=java_uuid,
            current_name=username,
            previous_name="Player",
        )

    async def resolve_claim(
        self,
        *,
        claim_id: int,
        status: ClaimStatus,
        resolved_by_account_id: int,
        reassign: bool = False,
    ) -> AliasClaim:
        self.reassign_requested = reassign
        claim = self.claims[claim_id]
        resolved = AliasClaim(
            claim.id,
            claim.alias_id,
            claim.alias_name,
            claim.account_id,
            status,
            claim.created_at,
            NOW,
            resolved_by_account_id,
        )
        self.claims[claim_id] = resolved
        return resolved

    async def replace_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None:
        self.created_code = (minecraft_uuid, code, username)


def username_lookup(username: str | None) -> Callable[[UUID], Awaitable[str | None]]:
    async def lookup(_minecraft_uuid: UUID) -> str | None:
        return username

    return lookup


def service(repository: FakeAccountRepository, username: str | None = "Player") -> AccountService:
    return AccountService(repository, username_lookup(username), lambda: 123456)


def test_identity_factories_use_canonical_provider_subjects() -> None:
    assert AccountIdentity.discord(123).subject == "123"
    assert AccountIdentity.java(JAVA_UUID, username="Player").subject == str(JAVA_UUID)
    assert AccountIdentity.bedrock(456, gamertag="Builder").subject == "456"
    with pytest.raises(ValueError, match="unsigned 64-bit"):
        AccountIdentity.bedrock(0)


async def test_discord_login_creates_an_account_without_making_discord_primary() -> None:
    repository = FakeAccountRepository()

    account = await service(repository).get_or_create_account(123)

    assert account.identity(IdentityProvider.DISCORD) == AccountIdentity.discord(123, verified_at=NOW)
    assert account.identity(IdentityProvider.JAVA) is None


async def test_grant_current_consent_updates_the_internal_account() -> None:
    repository = FakeAccountRepository()
    repository.seed_account(123)

    account = await service(repository).grant_current_consent(123)

    assert account.consent is not None
    assert account.consent.version == CURRENT_CONSENT_VERSION


async def test_consent_requires_an_existing_account() -> None:
    with pytest.raises(AccountNotFoundError):
        await service(FakeAccountRepository()).grant_current_consent(123)


async def test_link_rejects_an_invalid_or_conflicting_java_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    linked = service(repository)
    with pytest.raises(InvalidVerificationCodeError):
        await linked.link_minecraft_account(123, "bad", consent=CONSENT)

    repository.link_result = VerificationLinkResult(account=account, conflicting_java_uuid=OTHER_JAVA_UUID)
    with pytest.raises(AccountAlreadyLinkedError):
        await linked.link_minecraft_account(123, "valid", consent=CONSENT)


async def test_link_returns_the_automatically_claimed_alias() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT, java_uuid=JAVA_UUID)
    alias = CreatorAlias(1, "Player", account_id=account.id, claim_method=ClaimMethod.VERIFIED_IGN)
    repository.link_result = VerificationLinkResult(account=account, claimed_alias=alias)

    assert await service(repository).link_minecraft_account(123, "valid", consent=CONSENT) == alias


async def test_alias_claim_requires_current_consent() -> None:
    repository = FakeAccountRepository()
    repository.seed_account(123)
    repository.aliases[1] = CreatorAlias(1, "OldName")

    with pytest.raises(ConsentRequiredError):
        await service(repository).request_alias_claim(123, "OldName")


async def test_staff_resolution_records_the_staff_account() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    repository.aliases[1] = CreatorAlias(1, "OldName")
    claim = await service(repository).request_alias_claim(123, "OldName")

    resolved = await service(repository).approve_alias_claim(claim.id, staff_account_id=99)

    assert resolved.account_id == account.id
    assert resolved.resolved_by_account_id == 99
    assert resolved.status is ClaimStatus.APPROVED
    with pytest.raises(ClaimNotFoundError):
        await service(repository).approve_alias_claim(claim.id, staff_account_id=99)


async def test_merge_requires_recent_proof_of_two_distinct_accounts() -> None:
    repository = FakeAccountRepository()
    repository.merge_result = AccountMerge(1, 2, UUID(int=1), UUID(int=2))
    accounts = service(repository)

    result = await accounts.merge_accounts(RecentAccountProof(1, NOW), RecentAccountProof(2, NOW), now=NOW)
    assert result == repository.merge_result

    with pytest.raises(InvalidMergeProofError):
        await accounts.merge_accounts(RecentAccountProof(1, NOW), RecentAccountProof(1, NOW), now=NOW)
    with pytest.raises(InvalidMergeProofError):
        await accounts.merge_accounts(
            RecentAccountProof(1, NOW.subtract(minutes=11)), RecentAccountProof(2, NOW), now=NOW
        )


async def test_code_generation_validates_java_identity() -> None:
    repository = FakeAccountRepository()
    generated = await service(repository).generate_verification_code(JAVA_UUID)
    assert generated == 123456
    assert repository.created_code == (JAVA_UUID, "123456", "Player")

    with pytest.raises(MinecraftAccountNotFoundError):
        await service(FakeAccountRepository(), None).generate_verification_code(JAVA_UUID)


async def test_refresh_looks_up_the_current_name_and_delegates() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None

    refresh = await service(repository, "RenamedPlayer").refresh_java_identity(account.id)

    assert repository.refreshed == (account.id, JAVA_UUID, "RenamedPlayer")
    assert refresh.renamed is True
    assert refresh.previous_name == "Player"
    assert refresh.current_name == "RenamedPlayer"


async def test_refresh_without_a_linked_java_identity_is_rejected() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT)
    assert account.id is not None

    with pytest.raises(NoLinkedMinecraftAccountError):
        await service(repository).refresh_java_identity(account.id)


async def test_refresh_of_a_uuid_mojang_no_longer_knows_is_rejected() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None

    with pytest.raises(MinecraftAccountNotFoundError):
        await service(repository, None).refresh_java_identity(account.id)


async def test_refresh_of_an_unknown_account_is_rejected() -> None:
    with pytest.raises(AccountNotFoundError):
        await service(FakeAccountRepository()).refresh_java_identity(999)


async def test_refresh_can_name_which_java_identity_to_refresh() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None

    with pytest.raises(NoLinkedMinecraftAccountError):
        await service(repository).refresh_java_identity(account.id, java_uuid=OTHER_JAVA_UUID)
