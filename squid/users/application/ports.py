"""User account application ports."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from squid.users.domain import (
    AliasClaim,
    ClaimMethod,
    ClaimStatus,
    CreatorAlias,
    CreatorProfile,
    UserAccount,
    UserConsent,
)


@dataclass(frozen=True, slots=True)
class VerificationLinkResult:
    """Outcome of atomically consuming a code and linking an account."""

    account: UserAccount | None = None
    claimed_alias: CreatorAlias | None = None
    conflicting_minecraft_uuid: UUID | None = None


class UserRepository(Protocol):
    """Persistence operations required by :class:`UserService`."""

    async def add(
        self,
        *,
        consent: UserConsent,
        discord_id: int | None = None,
        minecraft_uuid: UUID | None = None,
        ign: str | None = None,
    ) -> UserAccount: ...

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None: ...

    async def get_or_create_discord(self, discord_id: int) -> UserAccount: ...

    async def update(self, user: UserAccount) -> None: ...

    async def unlink_minecraft_account(self, discord_id: int) -> bool: ...

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None: ...

    async def get_creator_profile(self, public_id: UUID) -> CreatorProfile | None: ...

    async def claim_unclaimed_alias(self, *, user_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        """Claim the alias matching *name* only if nobody else holds it."""
        ...

    async def request_claim(self, *, name: str, user_id: int) -> AliasClaim: ...

    async def get_claim(self, claim_id: int) -> AliasClaim | None: ...

    async def pending_claims(self) -> Sequence[AliasClaim]: ...

    async def resolve_claim(self, *, claim_id: int, status: ClaimStatus, resolved_by_discord_id: int) -> AliasClaim: ...

    async def consume_code_and_link_account(
        self,
        *,
        discord_id: int,
        code: str,
        consent: UserConsent,
    ) -> VerificationLinkResult: ...

    async def replace_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None: ...
