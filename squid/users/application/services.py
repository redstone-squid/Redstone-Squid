"""User account application services."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from squid.exceptions import (
    AccountAlreadyLinkedError,
    InvalidUserError,
    InvalidVerificationCodeError,
    MinecraftAccountNotFoundError,
)
from squid.users.application.ports import UserRepository
from squid.users.domain import UserAccount


class UserService:
    """Orchestrate user creation and Minecraft account verification."""

    def __init__(
        self,
        repository: UserRepository,
        minecraft_username_lookup: Callable[[UUID], Awaitable[str | None]],
        verification_code_factory: Callable[[], int],
    ):
        self._repository = repository
        self._minecraft_username_lookup = minecraft_username_lookup
        self._verification_code_factory = verification_code_factory

    async def add_user(
        self, *, discord_id: int | None = None, minecraft_uuid: UUID | None = None, ign: str | None = None
    ) -> UserAccount:
        """Add a user with at least one identifying value."""
        if discord_id is None and minecraft_uuid is None and ign is None:
            msg = "At least one of discord_id, minecraft_uuid, or ign must be provided."
            raise InvalidUserError(msg)
        return await self._repository.add(discord_id=discord_id, minecraft_uuid=minecraft_uuid, ign=ign)

    async def link_minecraft_account(self, discord_id: int, code: str) -> None:
        """Link a Discord user using a valid, unexpired verification code."""
        verification_code = await self._repository.get_valid_verification_code(code)
        if verification_code is None:
            raise InvalidVerificationCodeError

        user = await self._repository.get_by_discord_id(discord_id)
        if user is None:
            await self._repository.add(
                discord_id=discord_id,
                minecraft_uuid=verification_code.minecraft_uuid,
                ign=verification_code.username,
            )
            return

        if user.minecraft_uuid is not None and user.minecraft_uuid != verification_code.minecraft_uuid:
            raise AccountAlreadyLinkedError(discord_id, user.minecraft_uuid)

        user.minecraft_uuid = verification_code.minecraft_uuid
        user.ign = verification_code.username
        await self._repository.update(user)

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        """Unlink a user's Minecraft account."""
        return await self._repository.unlink_minecraft_account(discord_id)

    async def generate_verification_code(self, minecraft_uuid: UUID) -> int:
        """Generate a verification code after validating the Minecraft account."""
        minecraft_username = await self._minecraft_username_lookup(minecraft_uuid)
        if minecraft_username is None:
            raise MinecraftAccountNotFoundError(minecraft_uuid)

        await self._repository.invalidate_codes(minecraft_uuid)
        code = self._verification_code_factory()
        await self._repository.create_verification_code(
            minecraft_uuid=minecraft_uuid, code=str(code), username=minecraft_username
        )
        return code
