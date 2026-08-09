"""Application runtime lifecycle tests."""

import asyncio
from unittest.mock import AsyncMock

from squid.runtime import ApplicationRuntime, BackgroundTaskSupervisor


async def test_application_runtime_closes_database() -> None:
    database = AsyncMock()
    runtime = ApplicationRuntime(AsyncMock(), database.close, AsyncMock())

    async with runtime:
        pass

    database.close.assert_awaited_once_with()


async def test_application_runtime_checks_readiness() -> None:
    readiness = AsyncMock()
    runtime = ApplicationRuntime(AsyncMock(), AsyncMock(), AsyncMock(), readiness)

    await runtime.ready()

    readiness.assert_awaited_once_with()


async def test_background_task_supervisor_cancels_and_awaits_periodic_work() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation() -> None:
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    supervisor = BackgroundTaskSupervisor()
    supervisor.start_periodic(operation, name="test-job", interval=60)
    await entered.wait()

    await supervisor.close()

    assert cancelled.is_set()
