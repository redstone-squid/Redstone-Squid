"""Infrastructure fixtures for integration tests."""

import sys
from collections.abc import AsyncGenerator, Generator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.core.config import testcontainers_config
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    """Run one PostgreSQL container for the integration-test session."""
    previous_ryuk_setting = testcontainers_config.ryuk_disabled
    if sys.platform == "win32":
        testcontainers_config.ryuk_disabled = True
    try:
        with PostgresContainer("postgres:17-alpine", driver="psycopg2") as container:
            yield container
    finally:
        testcontainers_config.ryuk_disabled = previous_ryuk_setting


@pytest.fixture
async def async_engine(postgres_container: PostgresContainer) -> AsyncGenerator[AsyncEngine, None]:
    """Create an async engine on the current pytest event loop."""
    url = postgres_container.get_connection_url(driver="asyncpg")
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def async_session_factory(async_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create sessions suitable for repositories that manage their own transactions."""
    return async_sessionmaker(async_engine, expire_on_commit=False)
