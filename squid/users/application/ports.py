"""User account application ports."""

from typing import Protocol
from uuid import UUID

from squid.users.domain import UserAccount, VerificationCode


class UserRepository(Protocol):
    """Persistence operations required by :class:`UserService`."""

    async def add(
        self, *, discord_id: int | None = None, minecraft_uuid: UUID | None = None, ign: str | None = None
    ) -> UserAccount: ...

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None: ...

    async def update(self, user: UserAccount) -> None: ...

    async def unlink_minecraft_account(self, discord_id: int) -> bool: ...

    async def get_valid_verification_code(self, code: str) -> VerificationCode | None: ...

    async def invalidate_codes(self, minecraft_uuid: UUID) -> None: ...

    async def create_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None: ...
