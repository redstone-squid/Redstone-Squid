"""Repository for managing users and verification codes in the database."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.repos._base import BaseAsyncRepository
from squid.db.schema import User
from squid.db.schema import VerificationCode as VerificationCodeModel
from squid.services.users import UserAccount, VerificationCode


class _UserModelRepository(BaseAsyncRepository[User]):
    model_type = User


class _VerificationCodeModelRepository(BaseAsyncRepository[VerificationCodeModel]):
    model_type = VerificationCodeModel


class UserRepository:
    """Repository for managing users and verification codes in the database."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self._session = session

    async def add(
        self, *, discord_id: int | None = None, minecraft_uuid: uuid.UUID | None = None, ign: str | None = None
    ) -> UserAccount:
        """Insert a new user and return its primary key."""
        async with self._session() as session:
            user = User(discord_id=discord_id, ign=ign or "", minecraft_uuid=minecraft_uuid)  # FIXME: Allow empty IGN
            repository = _UserModelRepository(session=session, auto_commit=True)
            user = await repository.add(user)
            return UserAccount(
                discord_id=user.discord_id,
                minecraft_uuid=user.minecraft_uuid,
                ign=user.ign,
            )

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None:
        """Return the user matching *discord_id* or *None* if not found."""
        async with self._session() as session:
            repository = _UserModelRepository(session=session)
            user = await repository.get_one_or_none(discord_id=discord_id)
            if user is None:
                return None
            return UserAccount(discord_id=discord_id, minecraft_uuid=user.minecraft_uuid, ign=user.ign)

    async def update(self, user: UserAccount) -> None:
        """Update the Minecraft details for an existing user."""
        if user.discord_id is None:
            msg = "Cannot update a Discord-linked account without a Discord ID."
            raise ValueError(msg)
        async with self._session() as session:
            repository = _UserModelRepository(session=session, auto_commit=True)
            stored_user = await repository.get_one(discord_id=user.discord_id)
            stored_user.minecraft_uuid = user.minecraft_uuid
            stored_user.ign = user.ign or ""
            await repository.update(stored_user)

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        """Unlink a user's Minecraft account.

        Args:
            discord_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        async with self._session() as session:
            repository = _UserModelRepository(session=session, auto_commit=True)
            user = await repository.get_one_or_none(discord_id=discord_id)
            if user is None:
                return False
            user.minecraft_uuid = None
            await repository.update(user)
        return True

    @staticmethod
    def hash_verification_code(code: str) -> str:  # FIXME: Implement proper hashing
        """Hash a verification code for storage."""
        return code

    async def get_valid_verification_code(self, code: str) -> VerificationCode | None:
        """Return a valid verification code matching the given code."""
        async with self._session() as session:
            repository = _VerificationCodeModelRepository(session=session)
            verification_code = await repository.get_one_or_none(
                VerificationCodeModel.expires > datetime.now(tz=UTC).replace(tzinfo=None),
                code=self.hash_verification_code(code),
                valid=True,
            )
            if verification_code is None:
                return None
            return VerificationCode(
                minecraft_uuid=verification_code.minecraft_uuid,
                username=verification_code.username,
            )

    async def invalidate_codes(self, minecraft_uuid: uuid.UUID) -> None:
        """Invalidate all verification codes for the given Minecraft UUID."""
        async with self._session() as session:
            repository = _VerificationCodeModelRepository(session=session, auto_commit=True)
            verification_codes = await repository.get_many(
                VerificationCodeModel.expires > datetime.now(tz=UTC).replace(tzinfo=None),
                minecraft_uuid=minecraft_uuid,
                valid=True,
            )
            for verification_code in verification_codes:
                verification_code.valid = False
            if verification_codes:
                await repository.update_many(verification_codes)

    async def create_verification_code(self, *, minecraft_uuid: uuid.UUID, code: str, username: str) -> None:
        """Insert a new verification code for the given Minecraft UUID and username."""
        code = self.hash_verification_code(code)
        async with self._session() as session:
            verification_code = VerificationCodeModel(minecraft_uuid=minecraft_uuid, code=code, username=username)
            repository = _VerificationCodeModelRepository(session=session, auto_commit=True)
            await repository.add(verification_code)
