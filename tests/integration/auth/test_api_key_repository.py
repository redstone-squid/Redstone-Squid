"""PostgreSQL coverage for the API-key persistence boundary."""

from collections.abc import AsyncGenerator
from typing import cast

import pytest
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.accounts.infrastructure.models import Account
from squid.auth.application import ApiKeyService
from squid.auth.infrastructure.models import ApiKey
from squid.auth.infrastructure.repository import PostgresApiKeyRepository
from squid.persistence.base import Base

_TABLES = [cast(Table, Account.__table__), cast(Table, ApiKey.__table__)]


@pytest.fixture
async def api_key_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def test_issue_persists_one_sorted_array_for_duplicate_unsorted_scopes(
    api_key_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del api_key_tables
    service = ApiKeyService(
        PostgresApiKeyRepository(async_session_factory),
        "test-api-key-pepper",
        token_bytes=lambda size: b"x" * size,
    )

    issued = await service.issue(
        label="CI",
        scopes=["vote.poll.cast", " build.submission.read ", "vote.poll.cast", "account.self.read"],
    )

    async with async_session_factory() as session:
        stored = await session.scalar(select(ApiKey.scopes).where(ApiKey.key_id == issued.key.key_id))
    assert stored == ["account.self.read", "build.submission.read", "vote.poll.cast"]
