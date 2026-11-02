"""PostgreSQL coverage for settings updates using Discord server IDs."""

from collections.abc import AsyncGenerator
from contextlib import nullcontext
from functools import partial

import pytest
from anyio import CancelScope, create_task_group, sleep
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.core.errors import InvalidStateError
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.settings.application import SettingsService
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
def service(
    settings_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> SettingsService:
    return SettingsService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))


async def test_set_updates_existing_server_and_preserves_other_settings(service: SettingsService) -> None:
    await service.set_channel(123, "Smallest", 1001)
    await service.set_channel(123, "Vote", 1002)
    await service.set_channel(456, "Smallest", 2001)

    await service.set_channel(123, "Smallest", 1003)

    assert await service.get(123, "Smallest") == 1003
    assert await service.get(123, "Vote") == 1002
    assert await service.get(456, "Smallest") == 2001


@pytest.mark.parametrize("locale", ["zh-CN", None])
async def test_set_locale_updates_or_clears_existing_server(
    service: SettingsService,
    locale: str | None,
) -> None:
    await service.set_channel(123, "Vote", 1002)
    await service.set_locale(123, "en")

    await service.set_locale(123, locale)

    assert await service.get_locale(123) == locale
    assert await service.get(123, "Vote") == 1002


async def test_guild_remove_persists_membership_and_allows_rejoining(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await service.set_channel(123, "Vote", 1002)
    await service.guild_joined(123)

    await service.guild_removed(123)

    async with async_session_factory() as session:
        row = await session.get(ServerSetting, 123)
        assert row is not None
        assert row.in_server is False
        assert row.voting_channel_id == 1002

    await service.guild_joined(123)

    async with async_session_factory() as session:
        row = await session.get(ServerSetting, 123)
        assert row is not None
        assert row.in_server is True
        assert row.voting_channel_id == 1002


async def test_locale_service_creates_missing_settings(service: SettingsService) -> None:
    await service.set_locale(123, "fr")
    assert await service.get_locale(123) == "fr"


async def test_uow_commit_returns_loaded_detached_entities(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)
    async with factory() as uow:
        settings = await uow.settings.get_or_create_for_update(123)
        settings.locale = "fr"
        assert inspect(settings).persistent
        await uow.commit()
    assert inspect(settings).detached
    assert settings.locale == "fr"
    settings.locale = "de"
    assert await service.get_locale(123) == "fr"


@pytest.mark.parametrize("raise_error", [False, True])
async def test_uow_exit_without_commit_rolls_back_all_rows(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
    raise_error: bool,
) -> None:
    await service.set_channel(123, "Vote", 1002)
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)
    with pytest.raises(RuntimeError, match="abort") if raise_error else nullcontext():
        async with factory() as uow:
            first = await uow.settings.get_or_create_for_update(123)
            first.locale = "fr"
            first.voting_channel_id = 1003
            first.in_server = False
            # A second query autoflushes the first change without committing it.
            second = await uow.settings.get_or_create_for_update(456)
            second.locale = "de"
            if raise_error:
                raise RuntimeError("abort")
    assert await service.get_locale(123) is None
    assert await service.get(123, "Vote") == 1002
    assert await service.get_all(456) == {}
    async with factory() as uow:
        first = await uow.settings.get(123)
        assert first is not None
        assert first.in_server


async def test_uow_commit_is_terminal_and_scope_cannot_be_reused(
    settings_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    uow = SqlAlchemyUnitOfWork(async_session_factory)
    with pytest.raises(InvalidStateError):
        await uow.commit()
    async with uow:
        writer = uow.settings
        await writer.get_or_create_for_update(123)
        await uow.commit()
        with pytest.raises(InvalidStateError):
            await writer.get_or_create_for_update(456)
        with pytest.raises(InvalidStateError):
            await uow.commit()
    with pytest.raises(InvalidStateError):
        await writer.get_or_create_for_update(456)
    with pytest.raises(InvalidStateError):
        async with uow:
            pytest.fail("scope was reused")


async def test_cancelled_uow_rolls_back_and_releases_its_connection(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)
    with CancelScope() as scope:
        async with factory() as uow:
            settings = await uow.settings.get_or_create_for_update(123)
            settings.locale = "fr"
            scope.cancel()
            await sleep(0)
    assert scope.cancelled_caught
    async with factory() as uow:
        settings = await uow.settings.get_or_create_for_update(123)
        assert settings.locale is None
        settings.locale = "de"
        await uow.commit()
    assert await service.get_locale(123) == "de"


async def test_concurrent_first_writes_preserve_different_settings(service: SettingsService) -> None:
    async with create_task_group() as tasks:
        tasks.start_soon(service.set_locale, 123, "fr")
        tasks.start_soon(service.set_channel, 123, "Vote", 1003)
        tasks.start_soon(service.guild_joined, 123)
    assert await service.get_locale(123) == "fr"
    assert await service.get(123, "Vote") == 1003


async def test_failed_commit_rolls_back_and_finishes_the_uow(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await service.set_channel(123, "Smallest", 1001)
    await service.set_channel(456, "Smallest", 1002)
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)
    async with factory() as uow:
        settings = await uow.settings.get_or_create_for_update(456)
        settings.locale = "fr"
        settings.smallest_channel_id = 1001
        with pytest.raises(IntegrityError):
            await uow.commit()
        with pytest.raises(InvalidStateError):
            await uow.commit()
    assert await service.get_locale(456) is None
    assert await service.get(456, "Smallest") == 1002


async def test_exception_after_commit_does_not_undo_the_commit(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)

    async def commit_then_fail() -> None:
        async with factory() as uow:
            settings = await uow.settings.get_or_create_for_update(123)
            settings.locale = "fr"
            await uow.commit()
            raise RuntimeError("after commit")

    with pytest.raises(RuntimeError, match="after commit"):
        await commit_then_fail()
    assert await service.get_locale(123) == "fr"


async def test_channel_reads_and_clears_preserve_missing_server_semantics(service: SettingsService) -> None:
    assert await service.get_all(789) == {}
    await service.guild_removed(789)
    assert await service.get_all(789) == {}
    await service.set_channel(123, "Vote", 1001)
    await service.guild_joined(456)
    assert await service.get_many(iter([123, 456, 789]), "Vote") == {123: 1001, 456: None}
    assert await service.get_many([], "Vote") == {}
    assert await service.get(789, "Vote") is None
    await service.clear(123, "Vote")
    assert await service.get_all(123) == {
        "Smallest": None,
        "Fastest": None,
        "First": None,
        "Builds": None,
        "Vote": None,
    }
    assert await service.get_all(789) == {}
