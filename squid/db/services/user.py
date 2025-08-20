"""User service layer implementing repository pattern for user management."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from squid.db.domain import User, get_minecraft_username
from squid.db.repos import UserRepository
from squid.db.schema import OrmUser as UserModel
from squid.db.schema import OrmVerificationCode


class UserNotFound(Exception):
    """Exception raised when a user is not found in the database."""

    def __init__(self, user_id: int):
        super().__init__(f"User with ID {user_id} not found.")
        self.user_id = user_id


class UserService:
    """Service for User domain logic and orchestration."""

    def __init__(self, session: AsyncSession, user_repository: UserRepository):
        self.session = session
        self.repo = user_repository

    async def create_user(self, user_id: int | None = None, ign: str | None = None) -> UserModel:
        """Create a new user in the system.

        Args:
            user_id: The user's Discord ID.
            ign: The user's in-game name.

        Returns:
            The ID of the new user.
        """
        async with self.session as session:
            user = await self.repo.add(user_id, ign)
            await session.commit()
        return user

    async def link_account(self, user_id: int, code: str) -> User:
        """Link a Discord account with a Minecraft account using a verification code.

        Args:
            user_id: The user's Discord ID.
            code: The verification code.

        Returns:
            The updated user if the accounts were successfully linked. Otherwise, an exception is raised.

        Raises:

        """
        async with self.session as session:
            user = await self.repo.get(user_id)
            if user is None:
                raise UserNotFound(user_id)
            minecraft_uuid = await self.repo.get_minecraft_uuid_by_code(code)
            await user.link_minecraft(minecraft_uuid)
            await session.commit()
        return result

    async def unlink_account(self, user_id: int) -> bool:
        """Unlink a user's Minecraft account from their Discord account.

        Args:
            user_id: The user's Discord ID.

        Returns:
            True if the accounts were successfully unlinked, False otherwise.
        """
        async with self.session as session:
            # Check if user exists
            user = await self.repo.get(user_id)
            if user is None:
                return False
            user.unlink_minecraft()
            await self.repo.add(user)
            await session.commit()
        return True

    async def request_verification_code(self, minecraft_uuid: UUID) -> str:
        """Request a verification code for linking a Minecraft account.

        Args:
            minecraft_uuid: The user's Minecraft UUID.

        Returns:
            The generated verification code.

        Raises:
            ValueError: minecraft_uuid does not match a valid Minecraft account.
        """
        async with self.session as session:
            minecraft_username = await get_minecraft_username(minecraft_uuid)
            if minecraft_username is None:
                raise ValueError(f"UUID {minecraft_uuid} does not match a valid Minecraft account.")

            await self.repo.invalidate_user_verification_codes(minecraft_uuid)
            code = await self.repo.generate_verification_code(minecraft_uuid, minecraft_username)
            await session.commit()
        return code

    async def validate_verification_code(self, minecraft_uuid: UUID, code: str) -> bool:
        """Validate a verification code for a user.

        Args:
            minecraft_uuid: The user's Minecraft UUID.
            code: The verification code, unhashed.

        Returns:
            True if the code is valid, False otherwise.
        """
        stmt = (
            select(OrmVerificationCode)
            .where(OrmVerificationCode.code == _hash(code))
            .where(OrmVerificationCode.minecraft_uuid == str(minecraft_uuid))
            .where(OrmVerificationCode.valid.is_(True))
            .where(OrmVerificationCode.expires > utcnow())
        )
        result = await self.session.execute(stmt)
        verification_code = result.scalar_one_or_none()
        if verification_code is None:
            return False
        return True

    async def invalidate_user_verification_codes(self, minecraft_uuid: UUID) -> None:
        """Invalidate all verification codes for a user.

        Args:
            minecraft_uuid: The user's Minecraft UUID.
        """
        stmt = (
            update(OrmVerificationCode)
            .where(OrmVerificationCode.minecraft_uuid == str(minecraft_uuid))
            .values(valid=False)
        )
        await self.session.execute(stmt)
