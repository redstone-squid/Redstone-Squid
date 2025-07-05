"""User service layer implementing repository pattern for user management."""

# AIDEV-NOTE: Repository pattern implementation for user management - replaces direct UserManager usage

import random
import uuid
from dataclasses import dataclass
from uuid import UUID

import aiohttp
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import User as UserModel
from squid.db.schema import VerificationCode
from squid.utils import utcnow


@dataclass(slots=True)
class User:
    """A user in the system, which can be linked to both Discord and Minecraft accounts."""

    id: int | None = None
    ign: str | None = None
    discord_id: int | None = None
    minecraft_uuid: uuid.UUID | None = None


class UserRepository:
    """Repository for User persistence and queries."""

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
            msg = "No user data provided."
            raise ValueError(msg)

        async with self.session() as session:
            user = UserModel()
            if user_id is not None:
                user.discord_id = user_id
            if ign is not None:
                user.ign = ign
            session.add(user)
            await session.flush()
            await session.commit()
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
            stmt = select(UserModel).where(UserModel.discord_id == user_id)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user:
                user.minecraft_uuid = verification_code.minecraft_uuid
                user.ign = verification_code.username
            else:
                user = UserModel(
                    discord_id=user_id, minecraft_uuid=verification_code.minecraft_uuid, ign=verification_code.username
                )
                session.add(user)

            await session.flush()
            await session.commit()
            return True

    async def unlink_minecraft_account(self, user_id: int) -> bool:
        """Unlink a user's Minecraft account from their Discord account.

        Args:
            user_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        async with self.session() as session:
            stmt = update(UserModel).where(UserModel.discord_id == user_id).values(minecraft_uuid=None)
            await session.execute(stmt)
            await session.flush()
            await session.commit()
            return True

    @staticmethod
    async def get_minecraft_username(user_uuid: UUID) -> str | None:
        """Get a user's Minecraft username from their UUID.

        Args:
            user_uuid: The user's Minecraft UUID.

        Returns:
            The user's Minecraft username. None if the UUID is invalid.
        """
        # https://wiki.vg/Mojang_API#UUID_to_Profile_and_Skin.2FCape
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"https://sessionserver.mojang.com/session/minecraft/profile/{user_uuid!s}") as response,
        ):
            if response.status == 200:
                data = await response.json()
                return data["name"]
            if response.status == 204:  # No content
                return None
            msg = f"Failed to get username for UUID {user_uuid}. The Mojang API returned status code {response.status}."
            raise ValueError(msg)

    async def generate_verification_code(self, user_uuid: UUID) -> int:
        """Generate a new verification code for a user and invalidate any existing ones.

        Args:
            user_uuid: The user's Minecraft UUID.

        Returns:
            The generated verification code.

        Raises:
            ValueError: user_uuid does not match a valid Minecraft account.
        """
        async with self.session() as session:
            await self.invalidate_user_verification_codes(user_uuid)
            code = random.randint(100000, 999999)
            username = await self.get_minecraft_username(user_uuid)
            if username is None:
                msg = f"User {user_uuid} does not match a valid Minecraft account."
                raise ValueError(msg)
            verification_code = VerificationCode(minecraft_uuid=user_uuid, code=str(code), username=username)
            session.add(verification_code)
            await session.flush()
            await session.commit()
            return code

    async def validate_verification_code(self, user_uuid: UUID, code: str) -> bool:
        """Validate a verification code for a user.

        Args:
            user_uuid: The user's Minecraft UUID.
            code: The verification code.

        Returns:
            True if the code is valid, False otherwise.
        """
        async with self.session() as session:
            stmt = (
                select(VerificationCode)
                .where(VerificationCode.code == code)
                .where(VerificationCode.minecraft_uuid == str(user_uuid))
                .where(VerificationCode.valid.is_(True))
                .where(VerificationCode.expires > utcnow())
            )
            result = await session.execute(stmt)
            verification_code = result.scalar_one_or_none()
            return verification_code is not None

    async def invalidate_user_verification_codes(self, user_uuid: UUID) -> None:
        """Invalidate all verification codes for a user.

        Args:
            user_uuid: The user's Minecraft UUID.
        """
        async with self.session() as session:
            stmt = update(VerificationCode).where(VerificationCode.minecraft_uuid == str(user_uuid)).values(valid=False)
            await session.execute(stmt)
            await session.flush()
            await session.commit()


class UserService:
    """Service for User domain logic and orchestration."""

    def __init__(self, repository: UserRepository):
        self.repository = repository

    async def create_user(self, user_id: int | None = None, ign: str | None = None) -> int:
        """Create a new user in the system.

        Args:
            user_id: The user's Discord ID.
            ign: The user's in-game name.

        Returns:
            The ID of the new user.
        """
        return await self.repository.add_user(user_id, ign)

    async def link_account(self, user_id: int, code: str) -> bool:
        """Link a Discord account with a Minecraft account using a verification code.

        Args:
            user_id: The user's Discord ID.
            code: The verification code.

        Returns:
            True if the accounts were successfully linked, False otherwise.
        """
        return await self.repository.link_minecraft_account(user_id, code)

    async def unlink_account(self, user_id: int) -> bool:
        """Unlink a user's Minecraft account from their Discord account.

        Args:
            user_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        return await self.repository.unlink_minecraft_account(user_id)

    async def get_mc_username(self, user_uuid: UUID) -> str | None:
        """Get a user's Minecraft username from their UUID.

        Args:
            user_uuid: The user's Minecraft UUID.

        Returns:
            The user's Minecraft username. None if the UUID is invalid.
        """
        return await self.repository.get_minecraft_username(user_uuid)

    async def request_verification_code(self, user_uuid: UUID) -> int:
        """Request a verification code for linking a Minecraft account.

        Args:
            user_uuid: The user's Minecraft UUID.

        Returns:
            The generated verification code.

        Raises:
            ValueError: user_uuid does not match a valid Minecraft account.
        """
        minecraft_username = await self.repository.get_minecraft_username(user_uuid)
        if minecraft_username is None:
            msg = f"User {user_uuid} does not match a valid Minecraft account."
            raise ValueError(msg)

        return await self.repository.generate_verification_code(user_uuid)
