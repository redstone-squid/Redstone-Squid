"""User repository for managing user persistence."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.db.domain import User
from squid.db.schema import OrmUser


class UserRepository:
    """Repository for User persistence."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, user: User) -> None:
        """Add a user to the database."""
        if user.discord_id is None and user.minecraft_uuid is None:
            raise ValueError(f"User must have either a Discord ID or a Minecraft UUID: {user}")
        orm_user = OrmUser.from_domain(user)
        self.session.add(orm_user)
        await self.session.flush()

    async def get(self, user_id: int) -> User | None:
        """Get a user by their Discord ID.

        Args:
            user_id: The user's Discord ID.

        Returns:
            The user if found, None otherwise.
        """
        stmt = select(OrmUser).where(OrmUser.discord_id == user_id)
        result = await self.session.execute(stmt)
        orm_user = result.scalar_one_or_none()
        if orm_user is None:
            return None
        return orm_user.to_domain()
