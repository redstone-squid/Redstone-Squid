"""PostgreSQL coverage for settings updates using Discord server IDs."""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.settings.infrastructure.repository import SettingsRepository
from squid.settings.models import ServerSetting

_TABLES = [Base.metadata.tables["server_settings"]]


@pytest.fixture
async def settings_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=_TABLES)


@pytest.fixture
def repository(
    settings_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> SettingsRepository:
    return SettingsRepository(async_session_factory)


async def test_set_updates_existing_server_and_preserves_other_settings(repository: SettingsRepository) -> None:
    await repository.set(123, Smallest=1001, Vote=1002)
    await repository.set(456, Smallest=2001)

    await repository.set(123, Smallest=1003)

    assert await repository.get_single(123, "Smallest") == 1003
    assert await repository.get_single(123, "Vote") == 1002
    assert await repository.get_single(456, "Smallest") == 2001


@pytest.mark.parametrize("locale", ["zh-CN", None])
async def test_set_locale_updates_or_clears_existing_server(
    repository: SettingsRepository,
    locale: str | None,
) -> None:
    await repository.set_locale(123, "en")

    await repository.set_locale(123, locale)

    assert await repository.get_locale(123) == locale


async def test_guild_remove_persists_membership_and_allows_rejoining(
    repository: SettingsRepository,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await repository.set(123, Vote=1002)
    await repository.on_guild_join(123)

    await repository.on_guild_remove(123)

    async with async_session_factory() as session:
        row = await session.get(ServerSetting, 123)
        assert row is not None
        assert row.in_server is False
        assert row.voting_channel_id == 1002

    await repository.on_guild_join(123)

    async with async_session_factory() as session:
        row = await session.get(ServerSetting, 123)
        assert row is not None
        assert row.in_server is True
        assert row.voting_channel_id == 1002
