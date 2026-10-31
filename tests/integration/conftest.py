"""Infrastructure fixtures for integration tests."""

import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Generator

import psycopg2
import pytest
from alembic.config import Config
from psycopg2 import sql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.core.config import testcontainers_config
from testcontainers.postgres import PostgresContainer

from alembic import command


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer]:
    """Run one PostgreSQL container for the integration-test session."""
    previous_ryuk_setting = testcontainers_config.ryuk_disabled
    if sys.platform == "win32":
        testcontainers_config.ryuk_disabled = True
    try:
        with PostgresContainer("pgvector/pgvector:0.8.1-pg17", driver="psycopg2") as container:
            yield container
    finally:
        testcontainers_config.ryuk_disabled = previous_ryuk_setting


@pytest.fixture
async def async_engine(postgres_container: PostgresContainer) -> AsyncGenerator[AsyncEngine]:
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


@pytest.fixture
async def migrated_session_factory(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated database at Alembic head.

    Repository tests need the real schema, including the triggers and constraints that
    `alembic_utils` owns, so this runs the migration chain rather than `create_all`.
    """
    database_name = f"redstone_squid_{uuid.uuid4().hex}"
    admin_url = postgres_container.get_connection_url(driver="psycopg2")
    admin_dsn = admin_url.replace("postgresql+psycopg2://", "postgresql://")
    connection = psycopg2.connect(admin_dsn)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    finally:
        connection.close()

    migration_url = admin_url.rsplit("/", maxsplit=1)[0] + f"/{database_name}"
    monkeypatch.setenv("SQUID_DATABASE_URL", migration_url)
    command.upgrade(Config("alembic.ini", toml_file="pyproject.toml"), "head")
    engine = create_async_engine(migration_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        connection = psycopg2.connect(admin_dsn)
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
        finally:
            connection.close()
