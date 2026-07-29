from unittest.mock import AsyncMock

from squid.runtime import ApplicationRuntime


async def test_application_runtime_closes_database() -> None:
    database = AsyncMock()
    runtime = ApplicationRuntime(AsyncMock(), database.close, AsyncMock())

    async with runtime:
        pass

    database.close.assert_awaited_once_with()
