"""High-level operations for users"""

import random
from uuid import UUID

import aiohttp

from squid.db.repos.user_repository import UserRepository
from squid.db.schema import User


class VerificationError(ValueError):
    """Custom exception for verification-related errors."""


class UserService:
    """Domain service responsible for user-related business logic."""

    def __init__(self, user_repo: UserRepository):
        self._user_repo = user_repo

    async def add_user(
        self, *, discord_id: int | None = None, minecraft_uuid: UUID | None = None, ign: str | None = None
    ) -> User:
        """Add a new user.

        Raises:
            ValueError: If all parameters are None.
        """
        if discord_id is None and minecraft_uuid is None and ign is None:
            raise ValueError("At least one of discord_id, minecraft_uuid, or ign must be provided.")
        return await self._user_repo.add(discord_id=discord_id, minecraft_uuid=minecraft_uuid, ign=ign)

    async def link_minecraft_account(self, discord_id: int, code: str) -> None:
        """Using a verification code, link a user's Discord account with their Minecraft account.

        Args:
            discord_id: The user's Discord ID.
            code: The verification code.

        Raises:
            VerificationError: If the verification code is invalid or expired, or if the user already has a Minecraft account linked.
        """
        verification_code = await self._user_repo.get_valid_verification_code(code)
        if verification_code is None:
            raise VerificationError(f"Invalid or expired verification code: {code}. Please generate a new code.")

        user = await self._user_repo.get_by_discord_id(discord_id)
        if user is None:
            await self._user_repo.add(
                discord_id=discord_id,
                minecraft_uuid=verification_code.minecraft_uuid,
                ign=verification_code.username,
            )
            return

        if user.minecraft_uuid is not None and user.minecraft_uuid != verification_code.minecraft_uuid:
            raise VerificationError(
                f"User {discord_id} already has a Minecraft account linked: {user.minecraft_uuid}. "
                "Please unlink it before linking a new one."
            )
        user.minecraft_uuid = verification_code.minecraft_uuid
        user.ign = verification_code.username
        await self._user_repo.update(user)

    async def unlink_minecraft_account(self, user_id: int) -> bool:
        """Unlink a user's Minecraft account from their Discord account.

        Args:
            user_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        return await self._user_repo.unlink_minecraft_account(user_id)

    @staticmethod
    async def get_minecraft_username(minecraft_uuid: UUID) -> str | None:
        """Get a user's Minecraft username from their UUID.

        Args:
            minecraft_uuid: The user's Minecraft UUID.

        Returns:
            The user's Minecraft username. None if the UUID is invalid.
        """
        # https://wiki.vg/Mojang_API#UUID_to_Profile_and_Skin.2FCape
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://sessionserver.mojang.com/session/minecraft/profile/{minecraft_uuid!s}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["name"]
                if response.status == 204:  # No content
                    return None
                raise ValueError(
                    f"Failed to get username for UUID {minecraft_uuid}. The Mojang API returned status code {response.status}."
                )

    async def generate_verification_code(self, minecraft_uuid: UUID):
        """Generate a new verification code for a user and invalidate any existing ones.

        Args:
            minecraft_uuid: The user's Minecraft UUID.

        Returns:
            The generated verification code.

        Raises:
            ValueError: user_uuid does not match a valid Minecraft account.
        """
        minecraft_username = await self.get_minecraft_username(minecraft_uuid)
        if minecraft_username is None:
            raise ValueError(f"User {minecraft_uuid} does not match a valid Minecraft account.")

        await self._user_repo.invalidate_codes(minecraft_uuid)

        code = random.randint(100_000, 999_999)
        await self._user_repo.create_verification_code(
            minecraft_uuid=minecraft_uuid, code=str(code), username=minecraft_username
        )
        return code
