"""Account application ports, keyed by account rather than by identity provider."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.accounts.domain import (
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
    IdentityProvider,
    IdentityRefresh,
    LinkReservation,
    MergeTicket,
    ProfileUpdate,
)


@dataclass(frozen=True, slots=True)
class VerificationLinkResult:
    """Outcome of atomically consuming a code and linking an account."""

    account: Account | None = None
    claimed_alias: CreatorAlias | None = None
    conflicting_java_uuid: UUID | None = None
    reservation_expired: bool = False
    """The code was valid but the hold committing it had lapsed or been taken over.

    Distinct from an empty result so the caller reports an expired prompt rather than a bad code.
    """

    refresh: IdentityRefresh | None = None
    """The full name reconciliation, set on every path that consumed a code.

    `claimed_alias` duplicates one of its fields for callers that need nothing else.
    """


class AccountRepository(Protocol):
    """Persistence operations required by :class:`AccountService`.

    Every method is one transaction. Subjects arrive already in canonical form
    (`AccountIdentity.for_provider`) and alias names are matched case-insensitively by
    `fold_creator_name`.
    """

    async def create(
        self,
        *,
        consent: AccountConsent | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account:
        """Insert an account, its empty profile, and its identities atomically."""
        ...

    async def get_by_id(self, account_id: int) -> Account | None:
        """Return the account and its identities, or `None` when no account has that id."""
        ...

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]:
        """Return the accounts that exist, keyed by id; unknown ids are absent from the mapping."""
        ...

    async def get_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        """Return the account holding one canonical provider subject, or `None`."""
        ...

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        """Resolve one identity, creating its account when absent.

        *consent* is written only on a row this call creates; an existing account is returned
        untouched. Concurrent callers for the same subject converge on one account.
        """
        ...

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account:
        """Replace the account's privacy-notice receipt, raising `AccountNotFoundError`."""
        ...

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None:
        """Remove one identity by internal id, or return `None` when it is not this account's.

        An avatar sourced from the removed identity is cleared by the foreign key.
        """
        ...

    async def count_identities(self, account_id: int) -> int:
        """Return how many identities the account holds; 0 for an unknown account."""
        ...

    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        """Publish or withhold one identity, raising `AccountIdentityNotFoundError`."""
        ...

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None:
        """Record the provider rendering key, doing nothing when the identity is not this account's."""
        ...

    async def get_profile(self, account_id: int) -> AccountProfile | None:
        """Return the account's own profile, or `None` when no account has that id.

        An account whose profile row is missing reads as an empty profile rather than `None`.
        """
        ...

    async def upsert_profile(self, account_id: int, update_request: ProfileUpdate) -> AccountProfile:
        """Apply an already validated partial edit, creating the profile row if it is missing.

        Raises:
            AccountNotFoundError: no account has that id.
            AccountIdentityNotFoundError: the requested avatar identity is not this account's.
        """
        ...

    async def clear_profile(self, account_id: int) -> AccountProfile:
        """Erase the profile's content, keeping the owner's `hidden` flag, raising `AccountNotFoundError`."""
        ...

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket:
        """Store the digest of *code* as the account's only live ticket, raising `AccountNotFoundError`."""
        ...

    async def peek_merge_ticket(self, code: str) -> MergeTicket | None:
        """Return the live ticket for *code* without spending it, or `None`."""
        ...

    async def consume_merge_ticket(self, code: str) -> MergeTicket | None:
        """Spend the live ticket for *code*, or return `None` when it is unknown, expired or spent.

        Spending is a delete, so a replayed request cannot merge a second account into the survivor.
        """
        ...

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge:
        """Move every account-keyed resource to the survivor and delete the absorbed account.

        The absorbed public creator id survives as a permanent redirect.

        Raises:
            AccountNotFoundError: either id names no account.
        """
        ...

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None:
        """Return the alias whose folded name matches, or `None`."""
        ...

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None:
        """Return a creator's alias names, following merge redirects, or `None` when unknown."""
        ...

    async def get_creator_profile_record(self, public_id: UUID) -> CreatorProfileRecord | None:
        """Return everything known about one creator, unfiltered by visibility, following redirects."""
        ...

    async def claim_unclaimed_alias(self, *, account_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        """Credit *account_id* with the alias, or return `None` when it is unknown or already held."""
        ...

    async def request_claim(self, *, name: str, account_id: int) -> AliasClaim:
        """Open a pending claim, returning the account's existing pending one for the same alias.

        Raises:
            CreatorAliasNotFoundError: no alias carries that name.
            AliasAlreadyClaimedError: the alias is already credited to an account.
        """
        ...

    async def get_claim(self, claim_id: int) -> AliasClaim | None:
        """Return the claim with that id in any status, or `None`."""
        ...

    async def pending_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]:
        """List claims awaiting review, oldest first; *with_claimants* fills `AliasClaim.claimant`."""
        ...

    async def resolve_claim(
        self,
        *,
        claim_id: int,
        status: ClaimStatus,
        resolved_by_account_id: int,
        reassign: bool = False,
    ) -> AliasClaim:
        """Approve or reject a pending claim, returning it with its claimant loaded.

        Approval credits the claimant; *reassign* is required to take a name someone else holds.

        Raises:
            ClaimNotFoundError: no claim has that id, or it is no longer pending.
            AliasAlreadyClaimedError: the name is held and *reassign* is false.
        """
        ...

    async def consume_code_and_link_account(
        self,
        *,
        account_id: int,
        code: str,
        consent: AccountConsent,
        reservation_token: str | None = None,
    ) -> VerificationLinkResult:
        """Spend one verification code, attach its Java identity, and record *consent*, atomically.

        The account must already exist. Refusals come back as fields on `VerificationLinkResult`
        rather than exceptions: an empty result for a bad code, `reservation_expired` for a lapsed
        hold, `conflicting_java_uuid` for a clash. `refresh` is set on every path that consumed a
        code.

        Raises:
            AccountNotFoundError: no account has that id.
        """
        ...

    async def reserve_verification_code(self, code: str, *, ttl_seconds: int) -> LinkReservation | None:
        """Hold a live code for *ttl_seconds*, or return `None` when it is unknown, spent or held.

        Writes nothing identifying the reserver, so an abandoned prompt leaves no trace of it.
        """
        ...

    async def release_verification_code(self, code: str, reservation_token: str) -> bool:
        """Drop a hold early, returning whether *reservation_token* was the live one.

        Idempotent, and safe for a hold that already lapsed or was taken over.
        """
        ...

    async def refresh_java_identity(self, *, account_id: int, java_uuid: UUID, username: str) -> IdentityRefresh:
        """Store a freshly observed Java name and reconcile the creator credit that follows it.

        Raises:
            AccountNotFoundError: no account has that id.
            MinecraftAccountNotFoundError: the account holds no Java identity for that UUID.
        """
        ...

    async def replace_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None:
        """Invalidate the UUID's outstanding codes and store the digest of *code*, atomically."""
        ...

    async def verification_lockout(self, provider: IdentityProvider, subject: str) -> Instant | None:
        """Return when the identity's lockout ends, or `None` when it may attempt a code now."""
        ...

    async def record_verification_failure(
        self, provider: IdentityProvider, subject: str, *, max_failures: int, lockout_seconds: int
    ) -> Instant | None:
        """Charge one refused code, returning the lockout instant only when this call started one.

        A lockout already in progress reports `None`, so each lockout is announced once.
        """
        ...

    async def clear_verification_failures(self, provider: IdentityProvider, subject: str) -> None:
        """Forget the identity's consecutive failures after a successful redemption."""
        ...
