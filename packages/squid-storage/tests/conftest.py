"""Backend fixtures; PostgreSQL is opt-in locally and enabled in integration CI."""

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from asyncpg import Pool
from testcontainers.postgres import PostgresContainer

from .support import StorageBackend, isolated_postgres_pool


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("squid-storage")
    group.addoption("--storage-postgres", action="store_true", help="Run storage contracts in a PostgreSQL container")
    group.addoption(
        "--storage-postgres-dsn",
        default=os.getenv("SQUID_STORAGE_TEST_DSN"),
        help="Run storage contracts against this PostgreSQL DSN instead of Docker",
    )


@pytest.fixture(scope="session")
def storage_postgres_dsn(pytestconfig: pytest.Config) -> Iterator[str]:
    dsn = pytestconfig.getoption("--storage-postgres-dsn")
    if dsn:
        yield dsn
    elif pytestconfig.getoption("--storage-postgres"):
        with PostgresContainer("postgres:18", driver="psycopg2") as container:
            yield container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    else:
        pytest.skip("use --storage-postgres or --storage-postgres-dsn to run PostgreSQL contracts")


@pytest.fixture
async def postgres_pool(storage_postgres_dsn: str) -> AsyncIterator[Pool]:
    async with isolated_postgres_pool(storage_postgres_dsn) as pool:
        yield pool


@pytest.fixture(params=["memory", "sqlite", "postgres"])
async def backend(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[StorageBackend]:
    async with AsyncExitStack() as stack:
        pool = None
        if request.param == "postgres":
            dsn = request.getfixturevalue("storage_postgres_dsn")
            pool = await stack.enter_async_context(isolated_postgres_pool(dsn))
        backend = StorageBackend(request.param, tmp_path / "storage.sqlite3", pool)
        await backend.sessions.list()
        await backend.scoped.purge()
        yield backend
