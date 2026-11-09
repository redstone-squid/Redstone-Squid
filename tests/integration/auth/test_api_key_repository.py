"""PostgreSQL coverage for the API-key persistence boundary."""

from collections.abc import AsyncGenerator
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.auth.application import ApiKeyService
from squid.auth.models.entities import ApiKey
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.support.schema import model_tables

_TABLES = model_tables(Account, ApiKey)


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
        "test-api-key-pepper",
        uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory),
        token_bytes=lambda size: b"x" * size,
    )

    issued = await service.issue(
        label="CI",
        scopes=["vote.poll.cast", " build.submission.read ", "vote.poll.cast", "account.self.read"],
    )

    async with async_session_factory() as session:
        stored = await session.scalar(
            text("SELECT scopes FROM api_keys WHERE key_id = :key_id"), {"key_id": issued.key.key_id}
        )
    assert stored == ["account.self.read", "build.submission.read", "vote.poll.cast"]
