"""Process-local bookkeeping for task-reentrant build leases.

This only tracks which asyncio task currently holds a build's in-process
lease and how many times it has re-entered it. It has no database
dependency; the persisted lock flag is managed by whoever calls this
(currently :class:`squid.builds.infrastructure.repository.BuildRepository`).
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.errors import BuildBusyError
from squid.builds.infrastructure.models import Build
from squid.core.errors import InvalidStateError


class BuildLockTracker:
    """Track task ownership of in-process build leases."""

    def __init__(self) -> None:
        self._lock_owners: dict[int, tuple[asyncio.Task[object], int]] = {}

    @staticmethod
    def current_task() -> asyncio.Task[object]:
        """Return the running task, raising if called outside of one."""
        task = asyncio.current_task()
        if task is None:
            msg = "Build locks require a running asyncio task."
            raise InvalidStateError(msg)
        return cast(asyncio.Task[object], task)

    def is_held_locally(self, build_id: int) -> bool:
        """Return whether any task currently holds a local lease for *build_id*."""
        return build_id in self._lock_owners

    def try_reenter(self, build_id: int, task: asyncio.Task[object]) -> bool:
        """Bump the lease count if *task* already owns *build_id*'s lease."""
        lease = self._lock_owners.get(build_id)
        if lease is None:
            return False
        owner, count = lease
        if owner is not task:
            return False
        self._lock_owners[build_id] = (owner, count + 1)
        return True

    def record_acquired(self, build_id: int, task: asyncio.Task[object]) -> None:
        """Record that *task* has newly acquired *build_id*'s lease."""
        self._lock_owners[build_id] = (task, 1)

    def release(self, build_id: int) -> bool:
        """Release one level of *build_id*'s lease for the current task.

        Returns:
            True if this was the outermost release and the persisted lock
            should now be released too; False if a nested lease remains, or
            there was nothing to release.

        Raises:
            RuntimeError: If a task other than the current owner tries to release.
        """
        lease = self._lock_owners.get(build_id)
        if lease is None:
            return False
        owner, count = lease
        if owner is not self.current_task():
            msg = f"Build {build_id} lock can only be released by its owning task."
            raise InvalidStateError(msg, context={"build_id": build_id})
        if count > 1:
            self._lock_owners[build_id] = (owner, count - 1)
            return False
        self._lock_owners.pop(build_id, None)
        return True

    def clear(self) -> None:
        """Forget all locally-tracked leases."""
        self._lock_owners.clear()


class BuildLockRepository:
    """Manage task-reentrant leases backed by the persisted build lock."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._tracker = BuildLockTracker()

    async def acquire(self, build_id: int, *, blocking: bool = True, timeout: float = -1) -> bool:
        task = self._tracker.current_task()
        if self._tracker.try_reenter(build_id, task):
            return True
        if not blocking:
            return await self._try_acquire(build_id, task)

        sleep_time = 0.01
        started_at = time.monotonic()
        while True:
            if await self._try_acquire(build_id, task):
                return True
            if timeout >= 0 and time.monotonic() - started_at >= timeout:
                return False
            await asyncio.sleep(sleep_time)
            sleep_time = min(sleep_time * 1.5, 0.5)

    async def _try_acquire(self, build_id: int, task: asyncio.Task[object]) -> bool:
        if self._tracker.is_held_locally(build_id):
            return False
        async with self._session_factory() as session:
            statement = update(Build).where(Build.id == build_id, Build.is_locked.is_(False)).values(is_locked=True)
            result = cast(CursorResult[Any], await session.execute(statement))
            await session.commit()
        if result.rowcount == 1:
            self._tracker.record_acquired(build_id, task)
            return True
        return False

    async def release(self, build_id: int) -> None:
        if not self._tracker.release(build_id):
            return
        async with self._session_factory() as session:
            await session.execute(update(Build).where(Build.id == build_id).values(is_locked=False))
            await session.commit()

    @asynccontextmanager
    async def locked(self, build_id: int, *, timeout: float = 30) -> AsyncGenerator[None]:
        if not await self.acquire(build_id, timeout=timeout):
            raise BuildBusyError(build_id)
        try:
            yield
        finally:
            await self.release(build_id)

    async def clean_stale(self, *, older_than: Instant) -> None:
        async with self._session_factory() as session:
            await session.execute(update(Build).where(Build.locked_at < older_than.to_stdlib()).values(is_locked=False))
            await session.commit()
        self._tracker.clear()
