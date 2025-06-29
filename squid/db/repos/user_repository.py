"""Repository for managing users and verification codes in the database."""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import User, VerificationCode
from squid.utils import utcnow


class UserRepository:
    """Repository for managing users and verification codes in the database."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self._session = session

    async def add(self, *, discord_id: int | None = None, minecraft_uuid: uuid.UUID | None = None, ign: str | None = None) -> User:
        """Insert a new user and return its primary key."""
        async with self._session() as session:
            user = User(discord_id=discord_id, ign=ign, minecraft_uuid=minecraft_uuid)
            session.add(user)
            await session.flush()
            await session.commit()
            return user

    async def get_by_discord_id(self, discord_id: int) -> User | None:
        """Return the user matching *discord_id* or *None* if not found."""
        async with self._session() as session:
            stmt = select(User).where(User.discord_id == discord_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def update(self, user: User) -> None:
        """Update the Minecraft details for an existing user."""
        async with self._session() as session:
            session.add(user)
            await session.commit()

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        """Unlink a user's Minecraft account.

        Args:
            discord_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        async with self._session() as session:
            stmt = update(User).where(User.discord_id == discord_id).values(minecraft_uuid=None)
            result = await session.execute(stmt)
            if result.rowcount == 0:
                return False
            await session.commit()
        return True

    @staticmethod
    def hash_verification_code(code: str) -> str:  # FIXME: Implement proper hashing
        """Hash a verification code for storage."""
        return code

    async def get_valid_verification_code(self, code: str) -> VerificationCode | None:
        """Return a valid verification code matching the given code."""
        async with self._session() as session:
            stmt = (
                select(VerificationCode)
                .where(VerificationCode.code == self.hash_verification_code(code))
                .where(VerificationCode.expires > utcnow())
                .where(VerificationCode.valid.is_(True))
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def invalidate_codes(self, minecraft_uuid: uuid.UUID) -> None:
        """Invalidate all verification codes for the given Minecraft UUID."""
        async with self._session() as session:
            stmt = (
                update(VerificationCode)
                .where(VerificationCode.minecraft_uuid == str(minecraft_uuid))
                .where(VerificationCode.expires > utcnow())
                .values(valid=False)
            )
            await session.execute(stmt)
            await session.commit()

    async def create_verification_code(self, *, minecraft_uuid: uuid.UUID, code: str, username: str) -> None:
        """Insert a new verification code for the given Minecraft UUID and username."""
        code = self.hash_verification_code(code)
        async with self._session() as session:
            verification_code = VerificationCode(
                minecraft_uuid=minecraft_uuid, code=code, username=username
            )
            session.add(verification_code)
            await session.flush()
            await session.commit()
