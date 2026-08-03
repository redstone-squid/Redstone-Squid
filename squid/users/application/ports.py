"""User account application ports."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from squid.users.domain import AliasClaim, ClaimMethod, ClaimStatus, CreatorAlias, UserAccount, UserConsent
from squid.users.domain import VerificationCode as DomainVerificationCode


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

    async def update(self, user: UserAccount) -> None: ...

    async def unlink_minecraft_account(self, discord_id: int) -> bool: ...

    async def get_alias_by_name(self, name: str) -> CreatorAlias | None: ...

    async def claim_unclaimed_alias(self, *, user_id: int, name: str, method: ClaimMethod) -> CreatorAlias | None:
        """Claim the alias matching *name* only if nobody else holds it."""
        ...

    async def request_claim(self, *, name: str, user_id: int) -> AliasClaim: ...

    async def get_claim(self, claim_id: int) -> AliasClaim | None: ...

    async def pending_claims(self) -> Sequence[AliasClaim]: ...

    async def resolve_claim(self, *, claim_id: int, status: ClaimStatus, resolved_by_discord_id: int) -> AliasClaim: ...

    async def get_valid_verification_code(self, code: str) -> DomainVerificationCode | None: ...

    async def invalidate_codes(self, minecraft_uuid: UUID) -> None: ...

    async def create_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None: ...
