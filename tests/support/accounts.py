"""In-memory account state for tests that run the real `AccountService`.

Seed accounts, aliases and profiles here, then hand `InMemoryAccountService(repository)` to the
code under test. Reads follow the database repository's rules closely enough for screens and routes.
Linking and merging return scripted results instead of performing their database mutations, and
verification lockouts use the wall clock while everything else uses `NOW`.
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from uuid import UUID

from whenever import Instant

from squid.core.accounts.application import AccountService
from squid.core.accounts.application.ports import VerificationLinkResult
from squid.core.accounts.domain import (
    AccountConsent,
    AccountMerge,
    ClaimMethod,
    ClaimStatus,
    CreatorProfile,
    CreatorProfileRecord,
    CreditedAlias,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    ProfileUpdate,
    fold_creator_name,
)
from squid.core.accounts.errors import AccountIdentityNotFoundError, AccountNotFoundError, LastIdentityError
from squid.core.accounts.models import (
    Account,
    AccountIdentity,
    AccountProfile,
    AliasClaim,
    CataloguePreference,
    CreatorAlias,
    MergeTicket,
)
from tests.support.identity_uow import AccountScope

NOW = Instant.from_utc(2026, 8, 11, 12)


class FakeAccountRepository:
    """Account tables held in dictionaries. `AccountScope` gives each transaction a copy."""

    def __init__(self, *, next_id: int = 1) -> None:
        self.accounts: dict[int, Account] = {}
        self.aliases: dict[int, CreatorAlias] = {}
        self.claims: dict[int, AliasClaim] = {}
        self.link_result = VerificationLinkResult()
        self.created_code: tuple[UUID, str, str] | None = None
        self.taken_codes: set[str] = set()
        self.merge_result: AccountMerge | None = None
        self.refreshed: tuple[int, UUID, str] | None = None
        self.reservable: dict[str, LinkPreview] = {}
        self.reservations: set[str] = set()
        self.consumed_token: str | None = None
        self.failures: dict[tuple[IdentityProvider, str], int] = {}
        self.lockouts: dict[tuple[IdentityProvider, str], Instant] = {}
        self.preferences: dict[tuple[int, str], CataloguePreference] = {}
        self.profiles: dict[int, AccountProfile] = {}
        self.merge_tickets: dict[str, MergeTicket] = {}
        self.avatar_keys: dict[tuple[int, int], str | None] = {}
        self.next_id = next_id

    async def unlink_java_identity(self, discord_id: int) -> bool:
        raise NotImplementedError

    def seed_account(
        self,
        subject: int,
        *,
        provider: IdentityProvider = IdentityProvider.DISCORD,
        consent: AccountConsent | None = None,
        java_uuid: UUID | None = None,
        account_id: int | None = None,
    ) -> Account:
        """Store an account holding one provider identity, and a Java identity if given."""
        identities = [replace(AccountIdentity.for_provider(provider, str(subject), verified_at=NOW), id=1)]
        if java_uuid is not None:
            identities.append(replace(AccountIdentity.java(java_uuid, username="Player", verified_at=NOW), id=2))
        return self._store(identities, consent, account_id)

    def seed_alias(self, name: str, *, account: Account | None = None) -> CreatorAlias:
        """Store a credited name, claimed by `account` when one is given."""
        alias = CreatorAlias(
            id=len(self.aliases) + 1,
            name=name,
            account_id=None if account is None else account.id,
            claimed_at=None if account is None else NOW,
            claim_method=None if account is None else ClaimMethod.VERIFIED_IGN,
        )
        self.aliases[alias.id] = alias
        return alias

    def _store(
        self, identities: Sequence[AccountIdentity], consent: AccountConsent | None, account_id: int | None = None
    ) -> Account:
        if account_id is None:
            account_id = self.next_id
            self.next_id += 1
        account = Account(
            identities=tuple(identities),
            consent=consent,
            id=account_id,
            created_at=NOW,
            public_creator_id=UUID(int=account_id),
        )
        self.accounts[account_id] = account
        return account

    async def create(
        self,
        *,
        consent: AccountConsent | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account:
        return self._store(identities, consent)

    async def get_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        return next(
            (account for account in self.accounts.values() if account.identity_for(provider, subject) is not None),
            None,
        )

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket:
        self.merge_tickets = {
            digest: ticket for digest, ticket in self.merge_tickets.items() if ticket.account_id != account_id
        }
        ticket = MergeTicket(account_id=account_id, created_at=NOW, expires_at=NOW.add(seconds=ttl_seconds))
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
        """Set every ticket expiry before the fake clock."""
        self.merge_tickets = {
            digest: replace(ticket, expires_at=NOW.subtract(seconds=1)) for digest, ticket in self.merge_tickets.items()
        }

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None:
        account = self.accounts.get(account_id)
        if account is None:
            return None
        removed = next((identity for identity in account.identities if identity.id == identity_id), None)
        if removed is None:
            return None
        if len(account.identities) <= 1:
            raise LastIdentityError(account_id=account_id)
        remaining = tuple(identity for identity in account.identities if identity.id != identity_id)
        self.accounts[account_id] = _changed(account, identities=remaining)
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
        self.accounts[account_id] = _changed(
            account, identities=tuple(updated if i.id == identity_id else i for i in account.identities)
        )
        return updated

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        self.avatar_keys[(account_id, identity_id)] = avatar_key

    async def catalogue_preferences(self, account_id: int) -> list[CataloguePreference]:
        return [row for (owner, _), row in self.preferences.items() if owner == account_id]

    async def save_catalogue_preference(self, preference: CataloguePreference) -> None:
        key = preference.account_id, preference.scope
        if preference.filters or preference.sort:
            self.preferences[key] = preference
        else:
            self.preferences.pop(key, None)

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

    async def credit_owners(self, build_id: int) -> dict[int, UUID]:
        del build_id
        return {}

    async def account_id_for_public_id(self, public_id: UUID) -> int | None:
        return next(
            (account_id for account_id, account in self.accounts.items() if account.public_creator_id == public_id),
            None,
        )

    async def get_creator_profile_record(self, public_id: UUID) -> CreatorProfileRecord | None:
        account = next(
            (candidate for candidate in self.accounts.values() if candidate.public_creator_id == public_id),
            None,
        )
        # ORM identity columns may still be None before flush.
        if account is None or account.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            return None
        return CreatorProfileRecord(
            public_id=public_id,
            account_id=account.id,
            profile=self.profiles.get(account.id, AccountProfile.empty(account.id)),
            identities=account.identities,
            aliases=tuple(CreditedAlias(name) for name in self._alias_names(account.id)),
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

    async def lock_for_edit(self, account_id: int) -> Account:
        account = await self.get_by_id(account_id)
        if account is None:
            raise AccountNotFoundError(account_id)
        return account

    async def get_by_id(self, account_id: int) -> Account | None:
        return self.accounts.get(account_id)

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]:
        return {
            account_id: account for account_id in account_ids if (account := self.accounts.get(account_id)) is not None
        }

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        # Consent applies only when creating an account.
        return await self.get_by_identity(provider, subject) or self.seed_account(
            int(subject), provider=provider, consent=consent
        )

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account:
        account = self.accounts[account_id]
        # The database's version column advances on every row update.
        updated = _changed(account, consent=consent)
        updated.revision = account.revision + 1
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

    async def get_alias(self, alias_id: int) -> CreatorAlias | None:
        alias = next((alias for alias in self.aliases.values() if alias.id == alias_id), None)
        return None if alias is None else await self.get_alias_by_name(alias.name)

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        alias = next(
            (alias for alias in self.aliases.values() if fold_creator_name(alias.name) == fold_creator_name(name)),
            None,
        )
        if alias is None:
            return None
        holder = None if alias.account_id is None else self.accounts.get(alias.account_id)
        return replace(alias, public_creator_id=None if holder is None else holder.public_creator_id)

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        account = next((account for account in self.accounts.values() if account.public_creator_id == public_id), None)
        if account is None:
            return None
        return CreatorProfile(public_id, self._alias_names(account.id), canonical_public_id=public_id)

    def _alias_names(self, account_id: int) -> tuple[str, ...]:
        held = (alias.name for alias in self.aliases.values() if alias.account_id == account_id)
        return tuple(sorted(held, key=fold_creator_name))

    async def request_claim(self, *, name: str, account_id: int) -> AliasClaim:
        alias = await self.get_alias_by_name(name)
        assert alias is not None
        claim = AliasClaim(
            id=len(self.claims) + 1,
            alias_id=alias.id,
            alias_name=alias.name,
            account_id=account_id,
            status=ClaimStatus.PENDING,
            created_at=NOW,
        )
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
            id=claim.id,
            alias_id=claim.alias_id,
            alias_name=claim.alias_name,
            account_id=claim.account_id,
            status=status,
            created_at=claim.created_at,
            resolved_at=NOW,
            resolved_by_account_id=resolved_by_account_id,
        )
        self.claims[claim_id] = resolved
        return resolved

    async def replace_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> bool:
        if code in self.taken_codes:
            return False
        self.created_code = (minecraft_uuid, code, username)
        return True

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


def _changed(account: Account, **changes: object) -> Account:
    """Copy an account with changes, keeping its revision, which `replace` would reset."""
    updated = replace(account, **changes)  # type: ignore[arg-type]
    updated.revision = account.revision
    return updated


class InMemoryAccountService(AccountService):
    """The real account service over `repository`. Minecraft lookups answer `username`.

    Subclasses may override the calls a test records; everything else runs the real service.
    """

    def __init__(
        self,
        repository: FakeAccountRepository | None = None,
        *,
        username: str | None = "Player",
        verification_codes: Callable[[], int] = lambda: 123456,
    ) -> None:
        self.repository = FakeAccountRepository() if repository is None else repository

        async def lookup(_minecraft_uuid: UUID) -> str | None:
            return username

        super().__init__(lookup, verification_codes, uow_factory=lambda: AccountScope(self.repository))
