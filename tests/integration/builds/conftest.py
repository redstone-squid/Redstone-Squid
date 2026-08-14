"""Shared database fixtures for build integration tests."""

import uuid
from collections.abc import AsyncIterator

import psycopg2
import pytest
from alembic.config import Config
from psycopg2 import sql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command


@pytest.fixture
async def migrated_session_factory(
    postgres_container: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Create an isolated database at Alembic head for build repository tests."""
    database_name = f"redstone_squid_builds_{uuid.uuid4().hex}"
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
