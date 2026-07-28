from unittest.mock import AsyncMock

from squid.bootstrap import ApplicationRuntime


async def test_application_runtime_closes_database() -> None:
    database = AsyncMock()
    runtime = ApplicationRuntime(database, AsyncMock())

    async with runtime:
        pass

    database.close.assert_awaited_once_with()
