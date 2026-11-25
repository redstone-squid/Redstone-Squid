"""PostgreSQL coverage for settings updates using Discord server IDs."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import nullcontext
from functools import partial

import pytest
from anyio import CancelScope, create_task_group, sleep
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, AnonymousActor
from squid.core.errors import InvalidStateError, ValidationError
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.base import Base
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.settings.application import SettingsService
from squid.core.settings.domain import Setting
from squid.core.settings.domain.guild import CHANNEL_FIELDS
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.settings.infrastructure.repository import ensure_server_settings
from squid.core.settings.models import RuntimeSettingChange, ServerSetting
from tests.support.runtime_edits import edit_runtime

_TABLES = [
    Base.metadata.tables[name]
    for name in ("edit_operations", "accounts", "server_settings", "runtime_settings", "runtime_setting_changes")
]


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
    return SettingsService(
        permissions=PermissionService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory)),
        uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory),
    )


async def _seed_channel(service: SettingsService, server_id: int, setting: Setting, value: int | None) -> None:
    async with service._uow_factory() as uow:
        row = await uow.settings.get_or_create_for_update(server_id)
        setattr(row, CHANNEL_FIELDS[setting], value)
        await uow.commit()


async def _seed_locale(service: SettingsService, server_id: int, locale: str | None) -> None:
    async with service._uow_factory() as uow:
        row = await uow.settings.get_or_create_for_update(server_id)
        row.locale = locale
        await uow.commit()


async def test_guild_remove_persists_membership_and_allows_rejoining(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_channel(service, 123, "Vote", 1002)
    await service.guild_joined(123, context=ActingContext.direct(AnonymousActor()))

    await service.guild_removed(123, context=ActingContext.direct(AnonymousActor()))

    async with async_session_factory() as session:
        row = await session.get(ServerSetting, 123)
        assert row is not None
        assert row.in_server is False
        assert row.voting_channel_id == 1002

    await service.guild_joined(123, context=ActingContext.direct(AnonymousActor()))

    async with async_session_factory() as session:
        row = await session.get(ServerSetting, 123)
        assert row is not None
        assert row.in_server is True
        assert row.voting_channel_id == 1002


async def test_sync_presence_creates_missing_guilds_and_marks_absent_ones(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # 123 was left while offline, 456 is still present, 789 was joined while offline.
    await _seed_channel(service, 123, "Vote", 1002)
    await service.guild_joined(456, context=ActingContext.direct(AnonymousActor()))

    joined, left = await service.sync_presence([456, 789], context=ActingContext.direct(AnonymousActor()))

    assert (joined, left) == ([789], [123])
    async with async_session_factory() as session:
        rows = {row.server_id: row for row in (await session.scalars(select(ServerSetting))).all()}
    assert {server_id: row.in_server for server_id, row in rows.items()} == {123: False, 456: True, 789: True}
    assert rows[123].voting_channel_id == 1002
    assert await service.sync_presence([456, 789], context=ActingContext.direct(AnonymousActor())) == ([], [])
    assert await service.sync_presence([123, 456, 789], context=ActingContext.direct(AnonymousActor())) == ([123], [])


async def test_ensure_server_settings_creates_only_missing_rows(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_locale(service, 123, "fr")

    async with async_session_factory() as session, session.begin():
        created = await ensure_server_settings(session, [123, None, 456, 456])

    assert created == [456]
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) == "fr"
    assert await service.get_all(456, context=ActingContext.direct(AnonymousActor())) == dict.fromkeys(
        ("Smallest", "Fastest", "First", "Builds", "Vote")
    )


async def test_forget_channels_clears_only_matching_settings(service: SettingsService) -> None:
    await _seed_channel(service, 123, "Vote", 1002)
    await _seed_channel(service, 123, "Builds", 1003)
    await _seed_channel(service, 123, "Fastest", 1004)

    assert await service.referenced_channels(123, context=ActingContext.direct(AnonymousActor())) == {1002, 1003, 1004}
    assert set(
        await service.forget_channels(123, {1002, 1004, 9999}, context=ActingContext.direct(AnonymousActor()))
    ) == {
        "Vote",
        "Fastest",
    }

    assert await service.referenced_channels(123, context=ActingContext.direct(AnonymousActor())) == {1003}
    assert await service.forget_channels(456, {1003}, context=ActingContext.direct(AnonymousActor())) == ()


async def test_locale_service_creates_missing_settings(service: SettingsService) -> None:
    await _seed_locale(service, 123, "fr")
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) == "fr"


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
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) == "fr"


@pytest.mark.parametrize("raise_error", [False, True])
async def test_uow_exit_without_commit_rolls_back_all_rows(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
    raise_error: bool,
) -> None:
    await _seed_channel(service, 123, "Vote", 1002)
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
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) is None
    assert await service.get(123, "Vote", context=ActingContext.direct(AnonymousActor())) == 1002
    assert await service.get_all(456, context=ActingContext.direct(AnonymousActor())) == {}
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
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) == "de"


async def test_concurrent_first_writes_preserve_different_settings(service: SettingsService) -> None:
    async with create_task_group() as tasks:
        tasks.start_soon(_seed_locale, service, 123, "fr")
        tasks.start_soon(_seed_channel, service, 123, "Vote", 1003)
        tasks.start_soon(partial(service.guild_joined, context=ActingContext.worker("guild-join")), 123)
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) == "fr"
    assert await service.get(123, "Vote", context=ActingContext.direct(AnonymousActor())) == 1003


async def test_failed_commit_rolls_back_and_finishes_the_uow(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_channel(service, 123, "Smallest", 1001)
    await _seed_channel(service, 456, "Smallest", 1002)
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)
    async with factory() as uow:
        settings = await uow.settings.get_or_create_for_update(456)
        settings.locale = "fr"
        # This channel already belongs to server 123, so the uniqueness constraint rejects the commit.
        settings.smallest_channel_id = 1001
        with pytest.raises(IntegrityError):
            await uow.commit()
        with pytest.raises(InvalidStateError):
            await uow.commit()
    assert await service.get_locale(456, context=ActingContext.direct(AnonymousActor())) is None
    assert await service.get(456, "Smallest", context=ActingContext.direct(AnonymousActor())) == 1002


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
    assert await service.get_locale(123, context=ActingContext.direct(AnonymousActor())) == "fr"


async def test_channel_reads_and_clears_preserve_missing_server_semantics(service: SettingsService) -> None:
    assert await service.get_all(789, context=ActingContext.direct(AnonymousActor())) == {}
    await service.guild_removed(789, context=ActingContext.direct(AnonymousActor()))
    assert await service.get_all(789, context=ActingContext.direct(AnonymousActor())) == {}
    await _seed_channel(service, 123, "Vote", 1001)
    await service.guild_joined(456, context=ActingContext.direct(AnonymousActor()))
    assert await service.get_many(iter([123, 456, 789]), "Vote", context=ActingContext.direct(AnonymousActor())) == {
        123: 1001,
        456: None,
    }
    assert await service.get_many([], "Vote", context=ActingContext.direct(AnonymousActor())) == {}
    assert await service.get(789, "Vote", context=ActingContext.direct(AnonymousActor())) is None
    await _seed_channel(service, 123, "Vote", None)
    assert await service.get_all(123, context=ActingContext.direct(AnonymousActor())) == {
        "Smallest": None,
        "Fastest": None,
        "First": None,
        "Builds": None,
        "Vote": None,
    }
    assert await service.get_all(789, context=ActingContext.direct(AnonymousActor())) == {}


@pytest.fixture
async def actor_account_id(
    settings_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with async_session_factory() as session:
        account = Account()
        session.add(account)
        await session.commit()
        return account.id


async def test_runtime_edits_persist_and_audit_atomically(
    actor_account_id: int,
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert (await service.get_runtime(context=ActingContext.direct(AnonymousActor()))).welcome_forward_chance == 0.1
    await edit_runtime(service, {"welcome_forward_chance": 0.75}, actor_account_id=actor_account_id)
    fresh = SettingsService(
        permissions=PermissionService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory)),
        uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory),
    )
    assert (await fresh.get_runtime(context=ActingContext.direct(AnonymousActor()))).welcome_forward_chance == 0.75
    with pytest.raises(ValidationError):
        await edit_runtime(fresh, {"welcome_forward_chance": 2}, actor_account_id=actor_account_id)
    assert (await fresh.get_runtime(context=ActingContext.direct(AnonymousActor()))).welcome_forward_chance == 0.75
    async with async_session_factory() as session:
        changes = (await session.scalars(select(RuntimeSettingChange))).all()
        assert len(changes) == 1
        assert changes[0].actor_account_id == actor_account_id
        assert changes[0].before["welcome_forward_chance"] == 0.1
        assert changes[0].after["welcome_forward_chance"] == 0.75


async def test_runtime_defaults_are_seeded_only_once(
    service: SettingsService,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = SettingsService(
        permissions=PermissionService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory)),
        uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory),
        runtime_defaults=RuntimeSettings(welcome_channel_id=987),
    )
    assert (await first.get_runtime(context=ActingContext.direct(AnonymousActor()))).welcome_channel_id == 987
    assert (await service.get_runtime(context=ActingContext.direct(AnonymousActor()))).welcome_channel_id == 987


async def test_concurrent_runtime_patches_preserve_unrelated_values(
    service: SettingsService,
    actor_account_id: int,
) -> None:
    async def change(values: Mapping[str, object]) -> None:
        await edit_runtime(service, values, actor_account_id=actor_account_id)

    async with create_task_group() as group:
        group.start_soon(change, {"welcome_forward_chance": 0.8})
        group.start_soon(change, {"notification_retention_days": 30})
    settings = await service.get_runtime(context=ActingContext.direct(AnonymousActor()))
    assert settings.welcome_forward_chance == 0.8
    assert settings.notification_retention_days == 30


async def test_deleting_actor_preserves_settings_audit(
    service: SettingsService,
    actor_account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await edit_runtime(service, {"welcome_enabled": False}, actor_account_id=actor_account_id)
    async with async_session_factory() as session:
        await session.execute(delete(Account).where(Account.id == actor_account_id))
        await session.commit()
    async with async_session_factory() as session:
        audit = (await session.scalars(select(RuntimeSettingChange))).one()
        assert audit.actor_account_id is None
        assert audit.after["welcome_enabled"] is False
