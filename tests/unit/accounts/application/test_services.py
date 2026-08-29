"""Account application service tests."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from uuid import UUID

import pytest
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.application.ports import VerificationLinkResult
from squid.accounts.application.services import VERIFICATION_MAX_CONSECUTIVE_FAILURES
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
    CreditPreview,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
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
    LinkReservationExpiredError,
    MinecraftAccountNotFoundError,
    NoLinkedMinecraftAccountError,
    VerificationAttemptsExhaustedError,
)

JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_JAVA_UUID = UUID("22222222-2222-2222-2222-222222222222")
NOW = Instant.from_utc(2026, 8, 11, 12)
CONSENT = AccountConsent(CURRENT_CONSENT_VERSION, NOW)
ATTEMPT = (IdentityProvider.DISCORD, "123")


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
        self.reservable: dict[str, LinkPreview] = {}
        self.reservations: set[str] = set()
        self.consumed_token: str | None = None
        self.failures: dict[tuple[IdentityProvider, str], int] = {}
        self.lockouts: dict[tuple[IdentityProvider, str], Instant] = {}
        self._next_id = 1

    def seed_account(
        self,
        subject: int,
        *,
        provider: IdentityProvider = IdentityProvider.DISCORD,
        consent: AccountConsent | None = None,
        java_uuid: UUID | None = None,
    ) -> Account:
        identities = [AccountIdentity.for_provider(provider, str(subject), verified_at=NOW)]
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

    async def unlink_java_identity(self, account_id: int) -> bool:
        account = self.accounts.get(account_id)
        if account is None or account.identity(IdentityProvider.JAVA) is None:
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

    async def get_by_id(self, account_id: int) -> Account | None:
        return self.accounts.get(account_id)

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]:
        return {
            account_id: account for account_id in account_ids if (account := self.accounts.get(account_id)) is not None
        }

    async def get_or_create_identity(self, provider: IdentityProvider, subject: str) -> Account:
        return await self.get_by_identity(provider, subject) or self.seed_account(int(subject), provider=provider)

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account:
        account = self.accounts[account_id]
        updated = Account(account.identities, consent, account.id, account.created_at, account.public_creator_id)
        self.accounts[account_id] = updated
        return updated

    async def consume_code_and_link_account(
        self, *, account_id: int, code: str, consent: AccountConsent, reservation_token: str | None = None
    ) -> VerificationLinkResult:
        if account_id not in self.accounts:
            raise AccountNotFoundError(account_id)
        self.consumed_token = reservation_token
        if reservation_token is not None and reservation_token not in self.reservations:
            return VerificationLinkResult(reservation_expired=True)
        return self.link_result

    async def reserve_verification_code(self, code: str, *, ttl_seconds: int) -> LinkReservation | None:
        if code not in self.reservable:
            return None
        token = f"token-for-{code}"
        self.reservations.add(token)
        return LinkReservation(
            token=token,
            expires_at=NOW.add(seconds=ttl_seconds),
            preview=self.reservable[code],
        )

    async def release_verification_code(self, code: str, reservation_token: str) -> bool:
        held = reservation_token in self.reservations
        self.reservations.discard(reservation_token)
        return held

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

    async def pending_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        self.claimants_requested = with_claimants
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

    async def verification_lockout(self, provider: IdentityProvider, subject: str) -> Instant | None:
        return self.lockouts.get((provider, subject))

    async def record_verification_failure(
        self, provider: IdentityProvider, subject: str, *, max_failures: int, lockout_seconds: int
    ) -> Instant | None:
        key = (provider, subject)
        self.failures[key] = self.failures.get(key, 0) + 1
        if self.failures[key] < max_failures:
            return None
        self.failures[key] = 0
        self.lockouts[key] = Instant.now().add(seconds=lockout_seconds)
        return self.lockouts[key]

    async def clear_verification_failures(self, provider: IdentityProvider, subject: str) -> None:
        self.failures.pop((provider, subject), None)
        self.lockouts.pop((provider, subject), None)


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

    account = await service(repository).get_or_create_identity(IdentityProvider.DISCORD, "123")

    assert account.identity(IdentityProvider.DISCORD) == AccountIdentity.discord(123, verified_at=NOW)
    assert account.identity(IdentityProvider.JAVA) is None


async def test_grant_current_consent_updates_the_internal_account() -> None:
    repository = FakeAccountRepository()
    seeded = repository.seed_account(123)
    assert seeded.id is not None

    account = await service(repository).grant_current_consent(seeded.id)

    assert account.consent is not None
    assert account.consent.version == CURRENT_CONSENT_VERSION


async def test_link_rejects_an_invalid_or_conflicting_java_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    linked = service(repository)
    with pytest.raises(InvalidVerificationCodeError):
        await linked.link_minecraft_account(account.id, "bad", consent=CONSENT, attempted_by=ATTEMPT)

    repository.link_result = VerificationLinkResult(account=account, conflicting_java_uuid=OTHER_JAVA_UUID)
    with pytest.raises(AccountAlreadyLinkedError):
        await linked.link_minecraft_account(account.id, "valid", consent=CONSENT, attempted_by=ATTEMPT)


async def test_link_returns_the_automatically_claimed_alias() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT, java_uuid=JAVA_UUID)
    alias = CreatorAlias(1, "Player", account_id=account.id, claim_method=ClaimMethod.VERIFIED_IGN)
    repository.link_result = _linked(account, claimed_alias=alias)
    assert account.id is not None

    refresh = await service(repository).link_minecraft_account(
        account.id, "valid", consent=CONSENT, attempted_by=ATTEMPT
    )

    assert refresh.claimed_alias == alias


def _linked(account: Account, *, claimed_alias: CreatorAlias | None = None) -> VerificationLinkResult:
    """A successful redemption, which always carries the reconciliation it performed.

    `link_minecraft_account` returns that reconciliation rather than the claimed alias alone, so
    linking can report a contested credit in the same words a refresh does.
    """
    assert account.id is not None
    return VerificationLinkResult(
        account=account,
        claimed_alias=claimed_alias,
        refresh=IdentityRefresh(
            account_id=account.id,
            java_uuid=JAVA_UUID,
            current_name="Player",
            claimed_alias=claimed_alias,
        ),
    )


def _preview(**overrides: object) -> LinkPreview:
    defaults: dict[str, object] = {"java_uuid": JAVA_UUID, "username": "Player"}
    return LinkPreview(**(defaults | overrides))  # type: ignore[arg-type]


async def test_a_bad_code_fails_at_reservation_before_any_prompt() -> None:
    """The whole point of reserving first: the code is checked before the notice is read."""
    repository = FakeAccountRepository()

    with pytest.raises(InvalidVerificationCodeError):
        await service(repository).reserve_minecraft_link("bad", attempted_by=ATTEMPT)

    # And it costs the guesser a slot, because reserving is where guessing now happens.
    assert repository.failures[ATTEMPT] == 1


async def test_reserving_returns_the_preview_the_prompt_needs() -> None:
    repository = FakeAccountRepository()
    credit = CreditPreview(name="Player", build_count=12)
    repository.reservable["good"] = _preview(credit=credit)

    reservation = await service(repository).reserve_minecraft_link("good", attempted_by=ATTEMPT)

    assert reservation.preview.username == "Player"
    assert reservation.preview.credit == credit
    assert reservation.preview.credit is not None
    assert not reservation.preview.credit.is_contested
    assert reservation.token


async def test_reservation_lockout_is_enforced_before_reserving() -> None:
    """Otherwise reserving would be an uncapped oracle sitting next to a capped redemption."""
    repository = FakeAccountRepository()
    repository.reservable["good"] = _preview()
    accounts = service(repository)
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.reserve_minecraft_link("bad", attempted_by=ATTEMPT)

    with pytest.raises(VerificationAttemptsExhaustedError):
        await accounts.reserve_minecraft_link("good", attempted_by=ATTEMPT)


async def test_committing_a_reservation_passes_the_token_through() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.reservable["good"] = _preview()
    repository.link_result = _linked(account)
    accounts = service(repository)
    reservation = await accounts.reserve_minecraft_link("good", attempted_by=ATTEMPT)

    await accounts.link_minecraft_account(
        account.id, "good", consent=CONSENT, attempted_by=ATTEMPT, reservation=reservation
    )

    assert repository.consumed_token == reservation.token


async def test_a_lapsed_reservation_is_not_reported_as_a_bad_code() -> None:
    """A correct code told "invalid" sends the user to fetch a new one for the wrong reason."""
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.reservable["good"] = _preview()
    accounts = service(repository)
    reservation = await accounts.reserve_minecraft_link("good", attempted_by=ATTEMPT)
    await accounts.release_minecraft_link("good", reservation)

    with pytest.raises(LinkReservationExpiredError):
        await accounts.link_minecraft_account(
            account.id, "good", consent=CONSENT, attempted_by=ATTEMPT, reservation=reservation
        )


async def test_committing_a_reservation_is_not_charged_again() -> None:
    """The guess was already capped at reservation time; charging twice halves the real budget."""
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.reservable["good"] = _preview()
    accounts = service(repository)
    reservation = await accounts.reserve_minecraft_link("good", attempted_by=ATTEMPT)
    repository.link_result = VerificationLinkResult()

    # The code vanished between prompt and commit, which is not a guess.
    repository.reservations.add(reservation.token)
    with pytest.raises(InvalidVerificationCodeError):
        await accounts.link_minecraft_account(
            account.id, "good", consent=CONSENT, attempted_by=ATTEMPT, reservation=reservation
        )

    assert repository.failures.get(ATTEMPT, 0) == 0


async def test_consecutive_wrong_codes_lock_the_attempting_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    accounts = service(repository)

    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(account.id, "bad", consent=CONSENT, attempted_by=ATTEMPT)

    # The cap is now spent, so even a correct code is refused until the wait passes. That is the
    # point: the guess that succeeds is the one that takes over somebody else's Minecraft account.
    repository.link_result = _linked(account)
    with pytest.raises(VerificationAttemptsExhaustedError) as caught:
        await accounts.link_minecraft_account(account.id, "valid", consent=CONSENT, attempted_by=ATTEMPT)
    assert caught.value.retry_after > 0
    assert caught.value.public_context == {"retry_after": caught.value.retry_after}


async def test_a_lockout_is_scoped_to_one_identity() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    accounts = service(repository)
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(account.id, "bad", consent=CONSENT, attempted_by=ATTEMPT)

    # Anyone else is unaffected, so one attacker cannot deny the whole instance.
    repository.link_result = _linked(account)
    other = (IdentityProvider.DISCORD, "456")

    linked = await accounts.link_minecraft_account(account.id, "valid", consent=CONSENT, attempted_by=other)

    assert linked.account_id == account.id


async def test_a_success_clears_the_failure_count() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    accounts = service(repository)
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(account.id, "bad", consent=CONSENT, attempted_by=ATTEMPT)

    repository.link_result = _linked(account)
    await accounts.link_minecraft_account(account.id, "valid", consent=CONSENT, attempted_by=ATTEMPT)
    assert repository.failures.get(ATTEMPT, 0) == 0

    # Failures are consecutive, so the budget is whole again rather than one attempt from a lockout.
    repository.link_result = VerificationLinkResult()
    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES - 1):
        with pytest.raises(InvalidVerificationCodeError):
            await accounts.link_minecraft_account(account.id, "bad", consent=CONSENT, attempted_by=ATTEMPT)
    assert repository.lockouts == {}


async def test_holding_a_correct_code_is_never_charged_as_a_failure() -> None:
    """A conflict proves the caller had a valid code, so it must not spend their budget.

    Otherwise anyone whose account is already linked could be locked out by replaying their own
    successful code, turning the abuse control into the abuse.
    """
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.link_result = VerificationLinkResult(account=account, conflicting_java_uuid=OTHER_JAVA_UUID)
    accounts = service(repository)

    for _ in range(VERIFICATION_MAX_CONSECUTIVE_FAILURES + 2):
        with pytest.raises(AccountAlreadyLinkedError):
            await accounts.link_minecraft_account(account.id, "valid", consent=CONSENT, attempted_by=ATTEMPT)

    assert repository.failures == {}
    assert repository.lockouts == {}


async def test_alias_claim_requires_current_consent() -> None:
    repository = FakeAccountRepository()
    seeded = repository.seed_account(123)
    assert seeded.id is not None
    repository.aliases[1] = CreatorAlias(1, "OldName")

    with pytest.raises(ConsentRequiredError):
        await service(repository).request_alias_claim(seeded.id, "OldName")


async def test_staff_resolution_records_the_staff_account() -> None:
    repository = FakeAccountRepository()
    account = repository.seed_account(123, consent=CONSENT)
    assert account.id is not None
    repository.aliases[1] = CreatorAlias(1, "OldName")
    claim = await service(repository).request_alias_claim(account.id, "OldName")

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


async def test_an_account_without_discord_can_link_and_unlink_minecraft() -> None:
    """The point of the rekeying: a Bedrock-only caller is a first-class linker.

    Both halves were unreachable before -- linking looked the account up by Discord
    identity and minted one when absent, and unlinking looked it up by Discord identity
    and gave up when there was none.
    """
    repository = FakeAccountRepository()
    account = repository.seed_account(555, provider=IdentityProvider.BEDROCK, consent=CONSENT, java_uuid=JAVA_UUID)
    assert account.id is not None
    assert account.identity(IdentityProvider.DISCORD) is None
    alias = CreatorAlias(1, "Player", account_id=account.id, claim_method=ClaimMethod.VERIFIED_IGN)
    repository.link_result = _linked(account, claimed_alias=alias)
    accounts = service(repository)

    linked = await accounts.link_minecraft_account(account.id, "valid", consent=CONSENT, attempted_by=ATTEMPT)

    assert linked.claimed_alias == alias
    assert await accounts.unlink_minecraft_account(account.id) is True
    assert repository.accounts[account.id].identity(IdentityProvider.JAVA) is None


async def test_linking_against_a_missing_account_does_not_create_one() -> None:
    repository = FakeAccountRepository()

    with pytest.raises(AccountNotFoundError):
        await service(repository).link_minecraft_account(999, "valid", consent=CONSENT, attempted_by=ATTEMPT)

    assert repository.accounts == {}
