"""Build lock adapter tests."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.infrastructure.locks import BuildLockRepository
from squid.core.errors import InvalidStateError


async def test_build_lock_is_reentrant_only_for_owning_task() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.rowcount = 1
    session.execute.return_value = result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    locks = BuildLockRepository(cast(async_sessionmaker[AsyncSession], session_factory))

    assert await locks.acquire(42, blocking=False)
    assert await locks.acquire(42, blocking=False)

    async def contend() -> bool:
        return await locks.acquire(42, blocking=False)

    assert not await asyncio.create_task(contend())
    assert session.execute.await_count == 1

    await locks.release(42)
    assert session.execute.await_count == 1
    await locks.release(42)
    assert session.execute.await_count == 2
    release_statement = session.execute.await_args_list[-1].args[0]
    assert "builds.lock_token" in str(release_statement)


async def test_build_lock_rejects_release_from_another_task() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.rowcount = 1
    session.execute.return_value = result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    locks = BuildLockRepository(cast(async_sessionmaker[AsyncSession], session_factory))
    assert await locks.acquire(42, blocking=False)

    async def release() -> None:
        await locks.release(42)

    with pytest.raises(InvalidStateError, match="owning task"):
        await asyncio.create_task(release())

    await locks.release(42)
