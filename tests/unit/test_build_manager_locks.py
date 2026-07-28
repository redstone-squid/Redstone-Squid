"""Tests for task ownership of build persistence locks."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.build_manager import BuildManager


@pytest.mark.unit
async def test_build_lock_is_reentrant_only_for_owning_task() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.rowcount = 1
    session.execute.return_value = result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    manager = BuildManager(cast(async_sessionmaker[AsyncSession], session_factory))

    assert await manager.acquire_lock(42, blocking=False)
    assert await manager.acquire_lock(42, blocking=False)

    async def contend() -> bool:
        return await manager.acquire_lock(42, blocking=False)

    assert not await asyncio.create_task(contend())
    assert session.execute.await_count == 1

    await manager.release_lock(42)
    assert session.execute.await_count == 1
    await manager.release_lock(42)
    assert session.execute.await_count == 2


@pytest.mark.unit
async def test_build_lock_rejects_release_from_another_task() -> None:
    session = AsyncMock(spec=AsyncSession)
    result = Mock()
    result.rowcount = 1
    session.execute.return_value = result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session
    manager = BuildManager(cast(async_sessionmaker[AsyncSession], session_factory))
    assert await manager.acquire_lock(42, blocking=False)

    async def release() -> None:
        await manager.release_lock(42)

    with pytest.raises(RuntimeError, match="owning task"):
        await asyncio.create_task(release())

    await manager.release_lock(42)
