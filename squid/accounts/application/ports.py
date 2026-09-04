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

    Kept apart from an empty result so the caller can say the prompt expired rather than claim a
    correct code was invalid.
    """

    refresh: IdentityRefresh | None = None
    """The full name reconciliation, when the code was consumed.

    `claimed_alias` is the one field of it the original link flow needed and is kept for that
    caller; everything a rename produced — the previous name, retained credits, a contested
    name and the claim it opened — is here.
    """


class AccountMinecraftAuthorization(Protocol):
    """Account-owned consent and verified Java-identity authorization."""

    async def has_current_consent(self, account_id: int) -> bool:
        """Return whether the account has accepted the current privacy notice."""
        ...

    async def can_approve_minecraft_identity(self, *, account_id: int, java_uuid: UUID) -> bool:
        """Return whether current consent and exact verified Java ownership coexist."""
        ...


class AccountRepository(Protocol):
    """Persistence operations required by :class:`AccountService`."""

    async def create(
        self,
        *,
        consent: AccountConsent | None = None,
        identities: Sequence[AccountIdentity] = (),
    ) -> Account: ...

    async def get_by_id(self, account_id: int) -> Account | None: ...

    async def get_many(self, account_ids: Sequence[int]) -> dict[int, Account]: ...

    async def get_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None: ...

    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account: ...

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account: ...

    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity | None: ...

    async def count_identities(self, account_id: int) -> int: ...

    async def set_identity_visibility(
        self, account_id: int, identity_id: int, *, is_public: bool
    ) -> AccountIdentity: ...

    async def set_identity_avatar_key(self, account_id: int, identity_id: int, avatar_key: str | None) -> None: ...

    async def get_profile(self, account_id: int) -> AccountProfile | None: ...

    async def upsert_profile(self, account_id: int, update_request: ProfileUpdate) -> AccountProfile: ...

    async def clear_profile(self, account_id: int) -> AccountProfile: ...

    async def replace_merge_ticket(self, account_id: int, code: str, ttl_seconds: int) -> MergeTicket: ...

    async def peek_merge_ticket(self, code: str) -> MergeTicket | None: ...

    async def consume_merge_ticket(self, code: str) -> MergeTicket | None: ...

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge: ...

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None: ...

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None: ...

    async def get_creator_profile_record(self, public_id: UUID) -> CreatorProfileRecord | None: ...

    async def claim_unclaimed_alias(
        self, *, account_id: int, name: str, method: ClaimMethod
    ) -> CreatorAlias | None: ...

    async def request_claim(self, *, name: str, account_id: int) -> AliasClaim: ...

    async def get_claim(self, claim_id: int) -> AliasClaim | None: ...

    async def pending_claims(self, *, with_claimants: bool = False) -> Sequence[AliasClaim]: ...

    async def resolve_claim(
        self,
        *,
        claim_id: int,
        status: ClaimStatus,
        resolved_by_account_id: int,
        reassign: bool = False,
    ) -> AliasClaim: ...

    async def consume_code_and_link_account(
        self,
        *,
        account_id: int,
        code: str,
        consent: AccountConsent,
        reservation_token: str | None = None,
    ) -> VerificationLinkResult: ...

    async def reserve_verification_code(self, code: str, *, ttl_seconds: int) -> LinkReservation | None: ...

    async def release_verification_code(self, code: str, reservation_token: str) -> bool: ...

    async def refresh_java_identity(self, *, account_id: int, java_uuid: UUID, username: str) -> IdentityRefresh: ...

    async def replace_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None: ...

    async def verification_lockout(self, provider: IdentityProvider, subject: str) -> Instant | None: ...

    async def record_verification_failure(
        self, provider: IdentityProvider, subject: str, *, max_failures: int, lockout_seconds: int
    ) -> Instant | None: ...

    async def clear_verification_failures(self, provider: IdentityProvider, subject: str) -> None: ...
