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
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    IdentityProvider,
    IdentityRefresh,
)


@dataclass(frozen=True, slots=True)
class VerificationLinkResult:
    """Outcome of atomically consuming a code and linking an account."""

    account: Account | None = None
    claimed_alias: CreatorAlias | None = None
    conflicting_java_uuid: UUID | None = None
    refresh: IdentityRefresh | None = None
    """The full name reconciliation, when the code was consumed.

    `claimed_alias` is the one field of it the original link flow needed and is kept for that
    caller; everything a rename produced — the previous name, retained credits, a contested
    name and the claim it opened — is here.
    """


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

    async def get_or_create_identity(self, provider: IdentityProvider, subject: str) -> Account: ...

    async def update_consent(self, account_id: int, consent: AccountConsent) -> Account: ...

    async def unlink_java_identity(self, account_id: int) -> bool: ...

    async def merge(self, surviving_account_id: int, absorbed_account_id: int) -> AccountMerge: ...

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None: ...

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None: ...

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
    ) -> VerificationLinkResult: ...

    async def refresh_java_identity(self, *, account_id: int, java_uuid: UUID, username: str) -> IdentityRefresh: ...

    async def replace_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None: ...

    async def verification_lockout(self, provider: IdentityProvider, subject: str) -> Instant | None: ...

    async def record_verification_failure(
        self, provider: IdentityProvider, subject: str, *, max_failures: int, lockout_seconds: int
    ) -> Instant | None: ...

    async def clear_verification_failures(self, provider: IdentityProvider, subject: str) -> None: ...
