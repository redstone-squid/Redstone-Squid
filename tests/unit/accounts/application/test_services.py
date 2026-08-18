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
    MERGE_PROOF_MAX_AGE_SECONDS,
    MERGE_TICKET_TTL_SECONDS,
    Account,
    AccountConsent,
    AccountIdentity,
    AccountMerge,
    AccountProfile,
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    CreatorProfileRecord,
    CreditPreview,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    MergeTicket,
    ProfileLink,
    ProfileUpdate,
    RecentAccountProof,
    fold_creator_name,
)
from squid.accounts.errors import (
    AccountAlreadyLinkedError,
    AccountIdentityNotFoundError,
    AccountNotFoundError,
    ClaimNotFoundError,
    ConsentRequiredError,
    InvalidMergeCodeError,
    InvalidMergeProofError,
    InvalidVerificationCodeError,
    LastIdentityError,
    LinkReservationExpiredError,
    MinecraftAccountNotFoundError,
    NoLinkedMinecraftAccountError,
    VerificationAttemptsExhaustedError,
)
from squid.core.errors import ValidationError

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
        self.profiles: dict[int, AccountProfile] = {}
        self.merge_tickets: dict[str, MergeTicket] = {}
        self.avatar_keys: dict[tuple[int, int], str | None] = {}
        self._next_id = 1

    def seed_account(
        self,
        subject: int,
        *,
        provider: IdentityProvider = IdentityProvider.DISCORD,
        consent: AccountConsent | None = None,
        java_uuid: UUID | None = None,
    ) -> Account:
        identities = [replace(AccountIdentity.for_provider(provider, str(subject), verified_at=NOW), id=1)]
        if java_uuid is not None:
            identities.append(replace(AccountIdentity.java(java_uuid, username="Player", verified_at=NOW), id=2))
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

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket:
        self.merge_tickets = {
            digest: ticket for digest, ticket in self.merge_tickets.items() if ticket.account_id != account_id
        }
        ticket = MergeTicket(account_id, NOW, NOW.add(seconds=ttl_seconds))
        self.merge_tickets[code] = ticket
        return ticket

    async def peek_merge_ticket(self, code: str) -> MergeTicket | None:
        ticket = self.merge_tickets.get(code)
        return ticket if ticket is not None and ticket.is_live_at(NOW) else None

    async def consume_merge_ticket(self, code: str) -> MergeTicket | None:
        ticket = await self.peek_merge_ticket(code)
        if ticket is not None:
            del self.merge_tickets[code]
        return ticket

    def expire_merge_tickets(self) -> None:
        """Age every live ticket past its window, without waiting ten minutes."""
        self.merge_tickets = {
            digest: replace(ticket, expires_at=NOW.subtract(seconds=1)) for digest, ticket in self.merge_tickets.items()
        }

    async def count_identities(self, account_id: int) -> int:
        account = self.accounts.get(account_id)
        return 0 if account is None else len(account.identities)

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None:
        account = self.accounts.get(account_id)
        if account is None:
            return None
        removed = next((identity for identity in account.identities if identity.id == identity_id), None)
        if removed is None:
            return None
        remaining = tuple(identity for identity in account.identities if identity.id != identity_id)
        self.accounts[account_id] = replace(account, identities=remaining)
        return removed

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        account = self.accounts.get(account_id)
        target = (
            None
            if account is None
            else next((identity for identity in account.identities if identity.id == identity_id), None)
        )
        if account is None or target is None:
            raise AccountIdentityNotFoundError(identity_id, account_id=account_id)
        updated = replace(target, is_public=is_public)
        self.accounts[account_id] = replace(
            account,
            identities=tuple(updated if i.id == identity_id else i for i in account.identities),
        )
        return updated

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        self.avatar_keys[(account_id, identity_id)] = avatar_key

    async def get_profile(self, account_id: int) -> AccountProfile | None:
        if account_id not in self.accounts:
            return None
        return self.profiles.setdefault(account_id, AccountProfile.empty(account_id))

    async def upsert_profile(self, account_id: int, update_request: ProfileUpdate) -> AccountProfile:
        current = self.profiles.setdefault(account_id, AccountProfile.empty(account_id))
        avatar_identity_id = update_request.avatar_identity_id
        if isinstance(avatar_identity_id, int):
            account = self.accounts.get(account_id)
            owned = account is not None and any(i.id == avatar_identity_id for i in account.identities)
            if not owned:
                raise AccountIdentityNotFoundError(avatar_identity_id, account_id=account_id)
        updated = update_request.apply(current)
        self.profiles[account_id] = updated
        return updated

    async def clear_profile(self, account_id: int) -> AccountProfile:
        cleared = AccountProfile.empty(account_id)
        self.profiles[account_id] = cleared
        return cleared

    async def get_creator_profile_record(self, public_id: UUID) -> CreatorProfileRecord | None:
        account = next(
            (candidate for candidate in self.accounts.values() if candidate.public_creator_id == public_id),
            None,
        )
        if account is None or account.id is None:
            return None
        return CreatorProfileRecord(
            public_id=public_id,
            account_id=account.id,
            profile=self.profiles.get(account.id, AccountProfile.empty(account.id)),
            identities=account.identities,
            joined_at=account.created_at,
            canonical_public_id=public_id,
        )

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

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        # `consent` rides on creation only; an account that already exists is returned untouched.
        return await self.get_by_identity(provider, subject) or self.seed_account(
            int(subject), provider=provider, consent=consent
        )

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

    # The fake assigns identity ids because persistence does, and per-identity visibility and
    # unlink are both addressed by that id.
    assert account.identity(IdentityProvider.DISCORD) == replace(AccountIdentity.discord(123, verified_at=NOW), id=1)
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
    java = repository.accounts[account.id].identity(IdentityProvider.JAVA)
    assert java is not None
    assert java.id is not None
    await accounts.unlink_identity(account.id, java.id)
    assert repository.accounts[account.id].identity(IdentityProvider.JAVA) is None


async def test_linking_against_a_missing_account_does_not_create_one() -> None:
    repository = FakeAccountRepository()

    with pytest.raises(AccountNotFoundError):
        await service(repository).link_minecraft_account(999, "valid", consent=CONSENT, attempted_by=ATTEMPT)

    assert repository.accounts == {}


class TestProfiles:
    async def test_update_requires_current_consent(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1)
        assert account.id is not None

        with pytest.raises(ConsentRequiredError):
            await service(repository).update_profile(account.id, ProfileUpdate(display_name="Notch"))

    async def test_update_normalizes_through_the_domain(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None

        profile = await service(repository).update_profile(account.id, ProfileUpdate(display_name="  Notch  "))

        assert profile.display_name == "Notch"

    async def test_update_rejects_an_invalid_link(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None
        update = ProfileUpdate(links=(ProfileLink("Site", "javascript:alert(1)"),))

        with pytest.raises(ValidationError):
            await service(repository).update_profile(account.id, update)

    async def test_update_refuses_an_avatar_from_another_account(self) -> None:
        repository = FakeAccountRepository()
        owner = repository.seed_account(1, consent=CONSENT)
        repository.seed_account(2, consent=CONSENT)
        assert owner.id is not None

        with pytest.raises(AccountIdentityNotFoundError):
            await service(repository).update_profile(owner.id, ProfileUpdate(avatar_identity_id=9999))

    async def test_update_refuses_an_unknown_account(self) -> None:
        with pytest.raises(AccountNotFoundError):
            await service(FakeAccountRepository()).update_profile(404, ProfileUpdate(bio="hi"))

    async def test_clear_profile_resets_content_and_leaves_it_visible(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None
        accounts = service(repository)
        await accounts.update_profile(account.id, ProfileUpdate(display_name="Spam", hidden=True))

        cleared = await accounts.clear_profile(account.id)

        assert cleared.display_name is None
        assert not cleared.hidden

    async def test_public_profile_applies_visibility(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None
        assert account.public_creator_id is not None
        accounts = service(repository)
        await accounts.update_profile(account.id, ProfileUpdate(display_name="Notch", hidden=True))

        public = await accounts.get_public_profile(account.public_creator_id)

        assert public is not None
        assert public.hidden
        assert public.display_name is None

    async def test_public_profile_is_none_for_an_unknown_creator(self) -> None:
        assert await service(FakeAccountRepository()).get_public_profile(UUID(int=99)) is None


class TestIdentityManagement:
    async def test_list_identities_includes_hidden_ones(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
        assert account.id is not None
        accounts = service(repository)
        await accounts.set_identity_visibility(account.id, 2, is_public=False)

        identities = await accounts.list_identities(account.id)

        assert len(identities) == 2
        assert not next(identity for identity in identities if identity.id == 2).is_public

    async def test_unlink_removes_one_identity_and_keeps_the_other(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
        assert account.id is not None

        removed = await service(repository).unlink_identity(account.id, 2)

        assert removed.provider is IdentityProvider.JAVA
        assert [identity.provider for identity in repository.accounts[account.id].identities] == [
            IdentityProvider.DISCORD
        ]

    async def test_unlink_refuses_the_last_identity(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None

        with pytest.raises(LastIdentityError):
            await service(repository).unlink_identity(account.id, 1)

        assert len(repository.accounts[account.id].identities) == 1

    async def test_unlink_refuses_an_identity_the_account_does_not_hold(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
        assert account.id is not None

        with pytest.raises(AccountIdentityNotFoundError):
            await service(repository).unlink_identity(account.id, 9999)

    async def test_unlinking_does_not_touch_creator_credit(self) -> None:
        """Attribution is a fact about a build, not about how someone signs in."""
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT, java_uuid=JAVA_UUID)
        assert account.id is not None
        repository.aliases[1] = CreatorAlias(1, "Player", account_id=account.id)

        await service(repository).unlink_identity(account.id, 2)

        assert repository.aliases[1].account_id == account.id


class TestMergeCodes:
    """The two-sided proof: minting is the absorbed side, redeeming is the survivor."""

    async def test_a_minted_code_previews_and_completes(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        repository.merge_result = AccountMerge(survivor.id, absorbed.id, UUID(int=1), UUID(int=2))
        accounts = service(repository)

        code, ticket = await accounts.create_merge_code(absorbed.id)
        preview = await accounts.preview_merge(survivor.id, code)

        assert ticket.account_id == absorbed.id
        assert preview.absorbed_public_creator_id == absorbed.public_creator_id
        assert preview.identity_count == 1

        merge = await accounts.complete_merge(survivor.id, code, now=NOW)

        assert merge.surviving_account_id == survivor.id
        assert merge.absorbed_account_id == absorbed.id

    async def test_a_code_cannot_be_spent_twice(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        repository.merge_result = AccountMerge(survivor.id, absorbed.id, UUID(int=1), UUID(int=2))
        accounts = service(repository)
        code, _ = await accounts.create_merge_code(absorbed.id)
        await accounts.complete_merge(survivor.id, code, now=NOW)

        with pytest.raises(InvalidMergeCodeError):
            await accounts.complete_merge(survivor.id, code, now=NOW)

    async def test_minting_replaces_the_previous_code(self) -> None:
        """One live ticket per account is what keeps a short code safe."""
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        accounts = service(repository)

        first, _ = await accounts.create_merge_code(absorbed.id)
        await accounts.create_merge_code(absorbed.id)

        with pytest.raises(InvalidMergeCodeError):
            await accounts.preview_merge(survivor.id, first)

    async def test_an_unknown_code_is_refused(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        assert survivor.id is not None

        with pytest.raises(InvalidMergeCodeError):
            await service(repository).complete_merge(survivor.id, "NOTACODE")

    async def test_an_expired_ticket_is_refused(self) -> None:
        repository = FakeAccountRepository()
        survivor = repository.seed_account(1, consent=CONSENT)
        absorbed = repository.seed_account(2, consent=CONSENT)
        assert survivor.id is not None
        assert absorbed.id is not None
        accounts = service(repository)
        code, _ = await accounts.create_merge_code(absorbed.id)
        repository.expire_merge_tickets()

        with pytest.raises(InvalidMergeCodeError):
            await accounts.complete_merge(survivor.id, code, now=NOW)

    async def test_merging_an_account_into_itself_is_refused(self) -> None:
        repository = FakeAccountRepository()
        account = repository.seed_account(1, consent=CONSENT)
        assert account.id is not None
        accounts = service(repository)
        code, _ = await accounts.create_merge_code(account.id)

        with pytest.raises(InvalidMergeCodeError):
            await accounts.preview_merge(account.id, code)

    async def test_the_ticket_window_matches_the_proof_window(self) -> None:
        """A ticket must be redeemable for exactly as long as its proof is accepted.

        Two windows that could disagree would be a bug waiting for someone to tune one of them.
        """
        assert MERGE_TICKET_TTL_SECONDS == MERGE_PROOF_MAX_AGE_SECONDS


async def test_an_account_can_be_created_with_its_consent_receipt_in_one_write() -> None:
    """The Discord gate asks, then mints. Splitting that into create-then-record leaves a
    receipt-less account behind whenever the second call fails."""
    repository = FakeAccountRepository()
    account_service = service(repository)
    consent = AccountConsent.grant_current()

    account = await account_service.get_or_create_identity(IdentityProvider.DISCORD, "99", consent=consent)

    assert account.consent == consent
    assert not account.needs_consent_refresh


async def test_an_existing_account_is_not_handed_a_receipt_it_never_agreed_to() -> None:
    """`get_or_create_identity` cannot tell whether its caller asked anybody, so it only ever
    writes a receipt onto the row it creates itself."""
    repository = FakeAccountRepository()
    account_service = service(repository)
    existing = repository.seed_account(99, provider=IdentityProvider.DISCORD)

    account = await account_service.get_or_create_identity(
        IdentityProvider.DISCORD, "99", consent=AccountConsent.grant_current()
    )

    assert account.id == existing.id
    assert account.consent is None
