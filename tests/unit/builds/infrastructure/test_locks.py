"""Build lock adapter tests."""

import asyncio
import contextvars
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.infrastructure.locks import BuildLockRepository
from squid.core.errors import InvalidStateError


def _repository() -> tuple[BuildLockRepository, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.rowcount = 1
    result.all.return_value = []
    session.execute.return_value = result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    return BuildLockRepository(cast(async_sessionmaker[AsyncSession], session_factory)), session


async def test_build_lock_is_reentrant_for_the_holding_context() -> None:
    locks, session = _repository()

    assert await locks.acquire(42, blocking=False)
    assert await locks.acquire(42, blocking=False)
    assert session.execute.await_count == 1

    await locks.release(42)
    assert session.execute.await_count == 1
    await locks.release(42)
    assert session.execute.await_count == 2
    release_statement = session.execute.await_args_list[-1].args[0]
    assert "builds.lock_token" in str(release_statement)


async def test_child_task_reenters_the_lease_its_caller_holds() -> None:
    """A gather or task-group child inherits the caller's context and must not deadlock."""
    locks, session = _repository()
    assert await locks.acquire(42, blocking=False)

    async def reenter() -> bool:
        acquired = await locks.acquire(42, blocking=False)
        if acquired:
            await locks.release(42)
        return acquired

    assert await asyncio.create_task(reenter())
    # The child re-entered rather than contending, so no second persisted acquire.
    assert session.execute.await_count == 1

    await locks.release(42)
    assert session.execute.await_count == 2


async def test_foreign_context_cannot_acquire_a_held_lease() -> None:
    locks, session = _repository()
    assert await locks.acquire(42, blocking=False)

    async def contend() -> bool:
        return await locks.acquire(42, blocking=False)

    assert not await asyncio.create_task(contend(), context=contextvars.Context())
    assert session.execute.await_count == 1

    await locks.release(42)


async def test_build_lock_rejects_release_from_a_foreign_context() -> None:
    locks, _session = _repository()
    assert await locks.acquire(42, blocking=False)

    async def release() -> None:
        await locks.release(42)

    with pytest.raises(InvalidStateError, match="context holding it"):
        await asyncio.create_task(release(), context=contextvars.Context())

    await locks.release(42)


async def test_cancellation_inside_acquire_does_not_strand_the_lease() -> None:
    """The permanent-leak regression: a stranded lease blocks every later acquire."""
    locks, _session = _repository()

    started = asyncio.Event()

    async def hold_then_cancel() -> None:
        await locks.acquire(42, blocking=False)
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await locks.release(42)
            raise

    task = asyncio.create_task(hold_then_cancel())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await locks.acquire(42, blocking=False)
    await locks.release(42)


async def test_clean_stale_forgets_reclaimed_process_local_leases() -> None:
    locks, session = _repository()
    assert await locks.acquire(42, blocking=False)

    reclaimed = Mock()
    reclaimed.rowcount = 1
    reclaimed.all.return_value = [(42,)]
    session.execute.return_value = reclaimed

    await locks.clean_stale(older_than=Mock())

    # The persisted lock is gone, so a fresh context must be able to take it.
    async def take() -> bool:
        return await locks.acquire(42, blocking=False)

    assert await asyncio.create_task(take(), context=contextvars.Context())
