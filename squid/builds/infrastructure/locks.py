"""Persist token-fenced build leases in the caller's transaction."""

from collections.abc import Callable, Collection
from uuid import UUID

from sqlalchemy import func, or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.models import Build


class PostgresBuildLockRepository:
    """Change five-minute build leases within the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def acquire(self, build_id: int, token: UUID) -> bool:
        """Claim an unlocked or expired build; return False if busy or missing."""
        session = self._session()
        acquired = await session.scalar(
            update(Build)
            .where(Build.id == build_id, or_(Build.is_locked.is_(False), Build.lock_expires_at < func.now()))
            .values(is_locked=True, lock_token=token, lock_expires_at=func.now() + text("interval '5 minutes'"))
            .returning(Build.id)
        )
        return acquired is not None

    async def release(self, build_id: int, token: UUID) -> None:
        """Clear a lease only if its token still matches this worker's claim."""
        session = self._session()
        await session.execute(
            update(Build)
            .where(Build.id == build_id, Build.lock_token == token)
            .values(is_locked=False, lock_token=None, lock_expires_at=None)
        )

    async def clean_stale(self) -> Collection[int]:
        """Clear expired leases using the database clock and return their build IDs."""
        session = self._session()
        reclaimed = await session.scalars(
            update(Build)
            .where(Build.lock_expires_at < func.now())
            .values(is_locked=False, lock_token=None, lock_expires_at=None)
            .returning(Build.id)
        )
        return tuple(reclaimed)
