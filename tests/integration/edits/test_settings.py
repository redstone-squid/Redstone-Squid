"""Versioned declarative edits run through the production transaction boundary."""

from functools import partial

import anyio
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.core.actors import Actor
from squid.edits.application import EditExecutor
from squid.edits.domain import (
    ConcurrentEditError,
    EditCommand,
    EditResult,
    EditValues,
    LatestRevision,
    Revision,
    RevisionConflictError,
)
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.settings.application.edits import RUNTIME_EDIT, RUNTIME_TARGET, RuntimeSettingsEdit
from squid.settings.application.services import SettingsService
from squid.settings.domain.runtime import RuntimeSettings
from squid.settings.models import RuntimeSettingChange


async def owner(sessions: async_sessionmaker[AsyncSession]) -> Actor:
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        return Actor(account_id=account.id, is_bot_owner=True)


async def test_competing_settings_edits_have_one_winner_and_a_reversible_receipt(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    settings = SettingsService(uow_factory=factory)
    original, revision = await settings.runtime_snapshot()
    executor = EditExecutor(
        factory, {RUNTIME_EDIT: RuntimeSettingsEdit(PermissionService(uow_factory=factory), original)}
    )
    actor = await owner(migrated_session_factory)
    results: list[EditResult] = []
    conflicts: list[RevisionConflictError] = []

    async def edit(field: str) -> None:
        command = EditCommand(RUNTIME_EDIT, (Revision(RUNTIME_TARGET, revision),), EditValues.of({field: False}))
        try:
            results.append(await executor.execute(command, actor))
        except RevisionConflictError as error:
            conflicts.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(edit, "welcome_enabled")
        tasks.start_soon(edit, "redstoner_enabled")
    assert len(results) == len(conflicts) == 1
    receipt = results[0].receipt
    assert receipt is not None
    assert receipt.revisions[0].value == revision + 1
    reverted = await executor.execute(receipt.undo(receipt.revisions), actor)
    assert reverted.receipt is not None
    current, current_revision = await settings.runtime_snapshot()
    assert current == original
    assert current_revision == revision + 2
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(RuntimeSettingChange)) == 2

    # Ordinary writers advance the same root; a stale receipt cannot undo them.
    assert actor.account_id is not None
    await settings.update_runtime({"welcome_enabled": False}, actor_account_id=actor.account_id)
    with pytest.raises(RevisionConflictError):
        await executor.execute(receipt.redo(reverted.receipt.revisions), actor)


@pytest.mark.parametrize("explicit_flush", [False, True])
async def test_stale_orm_write_is_a_known_conflict_at_flush_or_commit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    explicit_flush: bool,
) -> None:
    defaults = RuntimeSettings()
    await SettingsService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)).get_runtime()
    async with (
        SqlAlchemyUnitOfWork(migrated_session_factory) as first,
        SqlAlchemyUnitOfWork(migrated_session_factory) as second,
    ):
        a = await first.settings.runtime(defaults)
        b = await second.settings.runtime(defaults)
        expected = Revision(RUNTIME_TARGET, b.revision)
        a.welcome_enabled = False
        await first.commit()
        b.redstoner_enabled = False
        operation = second.settings.flush_runtime_revision(b, expected) if explicit_flush else second.commit()
        with pytest.raises(ConcurrentEditError):
            await operation


async def test_unconditional_settings_edits_serialize_and_preserve_other_fields(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    settings = SettingsService(uow_factory=factory)
    original, revision = await settings.runtime_snapshot()
    executor = EditExecutor(
        factory, {RUNTIME_EDIT: RuntimeSettingsEdit(PermissionService(uow_factory=factory), original)}
    )
    actor = await owner(migrated_session_factory)
    results: list[EditResult] = []

    async def edit(field: str) -> None:
        results.append(
            await executor.execute(
                EditCommand(RUNTIME_EDIT, (LatestRevision(RUNTIME_TARGET),), EditValues.of({field: False})), actor
            )
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(edit, "welcome_enabled")
        tasks.start_soon(edit, "redstoner_enabled")
    current, current_revision = await settings.runtime_snapshot()
    assert not current.welcome_enabled
    assert not current.redstoner_enabled
    assert current_revision == revision + 2
    assert sorted(result.outcome.revisions[0].value for result in results) == [revision + 1, revision + 2]
