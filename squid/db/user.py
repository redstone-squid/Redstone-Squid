"""Handles user data and operations."""

import random
import uuid
from uuid import UUID

import aiohttp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import User, VerificationCode
from squid.utils import utcnow


class UserManager:
    """A class for managing user data and operations."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self.session = session

    async def add_user(self, user_id: int | None = None, ign: str | None = None) -> int:
        """Add a user to the database.

        Args:
            user_id: The user's Discord ID.
            ign: The user's in-game name.

        Returns:
            The ID of the new user.
        """
        if user_id is None and ign is None:
            raise ValueError("No user data provided.")

        async with self.session() as session:
            user = User()
            if user_id is not None:
                user.discord_id = user_id
            if ign is not None:
                user.ign = ign
            session.add(user)
            await session.flush()
            return user.id

    async def link_minecraft_account(self, user_id: int, code: str) -> bool:
        """Using a verification code, link a user's Discord account with their Minecraft account.

        Args:
            user_id: The user's Discord ID.
            code: The verification code.

        Returns:
            True if the code is valid and the accounts are linked, False otherwise.
        """
        async with self.session() as session:
            # Find valid verification code
            stmt = (
                select(VerificationCode)
                .where(VerificationCode.code == code)
                .where(VerificationCode.expires > utcnow())
                .where(VerificationCode.valid.is_(True))
            )
            result = await session.execute(stmt)
            verification_code = result.scalar_one_or_none()

            if verification_code is None:
                return False

            # Update or create user
            stmt = select(User).where(User.discord_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user:
                user.minecraft_uuid = verification_code.minecraft_uuid
                user.ign = verification_code.username
            else:
                user = User(
                    discord_id=user_id, minecraft_uuid=verification_code.minecraft_uuid, ign=verification_code.username
                )
                session.add(user)

            await session.flush()
            return True

    async def unlink_minecraft_account(self, user_id: int) -> bool:
        """Unlink a user's Minecraft account from their Discord account.

        Args:
            user_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        async with self.session() as session:
            stmt = update(User).where(User.discord_id == user_id).values(minecraft_uuid=None)
            await session.execute(stmt)
            await session.flush()
            return True

    async def get_minecraft_username(self, user_uuid: str | UUID) -> str | None:
        """Get a user's Minecraft username from their UUID.

        Args:
            user_uuid: The user's Minecraft UUID.

        Returns:
            The user's Minecraft username. None if the UUID is invalid.
        """
        # https://wiki.vg/Mojang_API#UUID_to_Profile_and_Skin.2FCape
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://sessionserver.mojang.com/session/minecraft/profile/{str(user_uuid)}"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["name"]
                elif response.status == 204:  # No content
                    return None
                else:
                    raise ValueError(
                        f"Failed to get username for UUID {user_uuid}. The Mojang API returned status code {response.status}."
                    )

    async def generate_verification_code(self, user_uuid: str | UUID) -> int:
        """Generate a new verification code for a user and invalidate any existing ones.

        Args:
            user_uuid: The user's Minecraft UUID.

        Returns:
            The generated verification code.

        Raises:
            ValueError: user_uuid does not match a valid Minecraft account.
        """
        if isinstance(user_uuid, str):
            user_uuid = uuid.UUID(user_uuid)
        minecraft_username = await self.get_minecraft_username(user_uuid)
        if minecraft_username is None:
            raise ValueError(f"User {user_uuid} does not match a valid Minecraft account.")

        async with self.session() as session:
            # Invalidate existing codes for this user
            stmt = (
                update(VerificationCode)
                .where(VerificationCode.minecraft_uuid == str(user_uuid))
                .where(VerificationCode.expires > utcnow())
                .values(valid=False)
            )
            await session.execute(stmt)

            # Create new verification code
            code = random.randint(100000, 999999)
            verification_code = VerificationCode(minecraft_uuid=user_uuid, code=str(code), username=minecraft_username)
            session.add(verification_code)
            await session.flush()
            return code
