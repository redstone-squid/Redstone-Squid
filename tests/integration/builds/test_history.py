"""Build history preserves persisted states across transactions and identity changes."""

from collections.abc import Callable
from functools import partial
from typing import cast

import anyio
import pytest
from alembic.config import Config
from sqlalchemy import Connection, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alembic import command
from squid.accounts.infrastructure.account_merge import merge_accounts
from squid.accounts.models import Account
from squid.builds.domain import Status
from squid.builds.errors import BuildRevisionMismatchError
from squid.builds.history import BuildChange
from squid.builds.history_models import BuildHistoryRecord, BuildHistoryState
from squid.builds.infrastructure.history import record_build_changes
from squid.builds.models import OtherBuild
from squid.core.pagination import PageSelector
from squid.persistence.account_merge import AccountMergeContext
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.schematics.infrastructure.preview_publisher import PostgresSchematicPreviewRepository


def change() -> BuildChange:
    return BuildChange(operation="edit", source="history_test", actor_system="test_worker")


async def seed(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        build = OtherBuild(submitter_account_id=account_id, submission_status=Status.PENDING, description="first")
        await uow.builds.save(build, change=change())
        await uow.commit()
        assert build.id is not None
        return build.id


async def edit(factory: Callable[[], SqlAlchemyUnitOfWork], build_id: int, description: str) -> None:
    async with factory() as uow:
        build = await uow.builds.get_by_id(build_id)
        assert build is not None
        build = build.copy_for_edit()
        build.description = description
        await uow.builds.save(build, change=change())
        await uow.commit()


async def test_adjacent_edits_share_states_and_paginate(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    build_id = await seed(sessions)
    await edit(factory, build_id, "second")
    await edit(factory, build_id, "third")
    async with sessions() as session:
        records = (await session.scalars(select(BuildHistoryRecord).order_by(BuildHistoryRecord.id))).all()
        assert len(records) == 3
        assert await session.scalar(select(func.count()).select_from(BuildHistoryState)) == 3
        assert records[1].before_state_id == records[0].after_state_id
        assert records[2].before_state_id == records[1].after_state_id
    async with factory() as uow:
        page = await uow.build_history.list_history(build_id, page_size=1)
        assert page.total == 3
        assert page.next is not None
        second = await uow.build_history.list_history(
            build_id, selector=PageSelector(after_id=page.next.after_id), page_size=1
        )
        assert second.items[0].id == records[1].id
        assert second.prev is not None
        back = await uow.build_history.list_history(
            build_id, selector=PageSelector(before_id=second.prev.before_id), page_size=1
        )
        assert back.items == page.items
        detail = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert detail is not None
        assert detail.before is not None
        assert detail.after is not None
        assert isinstance(detail.before.payload["build"], dict)
        assert detail.before.payload["build"]["description"] == "second"
        assert isinstance(detail.after.payload["build"], dict)
        assert detail.after.payload["build"]["description"] == "third"
        assert await uow.build_history.get_history_entry(build_id + 1, detail.id) is None
        assert (await uow.build_history.list_history(build_id + 1)).items == ()


async def test_noops_rollback_and_unrecorded_changes(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    build_id = await seed(sessions)
    await edit(factory, build_id, "first")
    async with factory() as uow:
        build = await uow.builds.get_by_id(build_id)
        assert build is not None
        draft = build.copy_for_edit()
        draft.description = "rolled back"
        await uow.builds.save(draft, change=change())
    async with sessions.begin() as session:
        await session.execute(text("UPDATE builds SET description = 'manual' WHERE id = :id"), {"id": build_id})
    await edit(factory, build_id, "second")
    async with factory() as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 2
        entry = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert entry is not None
        assert entry.before is not None
        assert isinstance(entry.before.payload["build"], dict)
        assert entry.before.payload["build"]["description"] == "manual"
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BuildHistoryState)) == 3


async def test_nested_preview_changes_coalesce(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    sessions = migrated_session_factory
    build_id = await seed(sessions)
    outer = BuildChange(operation="edit", source="test", actor_account_id=123)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        async with uow.builds.changes(outer):
            build = await uow.builds.get_by_id(build_id)
            assert build is not None
            draft = build.copy_for_edit()
            draft.description = "changed"
            await uow.builds.save(draft, change=outer)
            # The preview repository normally receives its session from the unit of work.
            await PostgresSchematicPreviewRepository.replace_generated_preview_link(
                uow._active_session(), build_id, "https://example.com/preview.png"
            )
        await uow.commit()
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 2
        assert page.items[0].change == outer
        entry = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert entry is not None
        assert entry.after is not None
        assert entry.after.payload["links"] == [{"media_type": "render", "url": "https://example.com/preview.png"}]


@pytest.mark.parametrize("table", ["build_history_states", "build_edit_history"])
@pytest.mark.parametrize(
    "statement", ["UPDATE {table} SET build_id = build_id", "DELETE FROM {table}", "TRUNCATE {table} CASCADE"]
)
async def test_database_rejects_history_mutations(
    migrated_session_factory: async_sessionmaker[AsyncSession], table: str, statement: str
) -> None:
    await seed(migrated_session_factory)
    async with migrated_session_factory() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement.format(table=table)))


async def test_stale_edits_and_history_failure_roll_back(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    build_id = await seed(sessions)
    async with factory() as uow:
        stale = await uow.builds.get_by_id(build_id)
        assert stale is not None
    await edit(factory, build_id, "new")
    async with factory() as uow:
        with pytest.raises(BuildRevisionMismatchError):
            await uow.builds.save(stale.copy_for_edit(), change=change())
    duplicate = change()
    async with factory() as uow:
        build = await uow.builds.get_by_id(build_id)
        assert build is not None
        await uow.builds.confirm(build, change=duplicate)
        await uow.commit()
    async with factory() as uow:
        build = await uow.builds.get_by_id(build_id)
        assert build is not None
        with pytest.raises(DBAPIError, match="unique"):
            await uow.builds.deny(build, change=duplicate)
    async with factory() as uow:
        build = await uow.builds.get_by_id(build_id)
        assert build is not None
        assert build.submission_status == Status.CONFIRMED
        assert (await uow.build_history.list_history(build_id)).total == 3


async def test_delete_preserves_inspection(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    sessions = migrated_session_factory
    build_id = await seed(sessions)
    async with sessions.begin() as session, record_build_changes(session, change(), (build_id,)):
        await session.execute(text("DELETE FROM builds WHERE id = :id"), {"id": build_id})
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 2
        entry = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert entry is not None
        assert entry.before is not None
        assert entry.after is None
        assert entry.change.operation == "delete"


async def test_concurrent_edits_record_only_the_winner(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_id = await seed(sessions)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        original = await uow.builds.get_by_id(build_id)
        assert original is not None
    results: list[str] = []

    async def write(description: str) -> None:
        draft = original.copy_for_edit()
        draft.description = description
        try:
            async with SqlAlchemyUnitOfWork(sessions) as uow:
                await uow.builds.save(draft, change=change())
                await uow.commit()
            results.append("saved")
        except BuildRevisionMismatchError:
            results.append("stale")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(write, "left")
        tasks.start_soon(write, "right")
    assert sorted(results) == ["saved", "stale"]
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        assert (await uow.build_history.list_history(build_id)).total == 2


async def test_account_merge_keeps_original_attribution(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_id = await seed(sessions)
    async with sessions.begin() as session:
        survivor = Account()
        session.add(survivor)
        await session.flush()
        survivor_id = survivor.id
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        build = await uow.builds.get_by_id(build_id)
        assert build is not None
        assert build.submitter_account_id is not None
        absorbed_id = build.submitter_account_id
        draft = build.copy_for_edit()
        draft.description = "authored"
        await uow.builds.save(draft, change=BuildChange(operation="edit", source="test", actor_account_id=absorbed_id))
        await uow.commit()
    async with sessions.begin() as session:
        await merge_accounts(session, AccountMergeContext(survivor_id, absorbed_id))
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 3
        assert page.items[0].change.operation == "account_merge"
        assert page.items[1].change.actor_account_id == absorbed_id
        entry = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert entry is not None
        assert entry.before is not None
        assert entry.after is not None
        assert isinstance(entry.before.payload["build"], dict)
        assert isinstance(entry.after.payload["build"], dict)
        assert entry.before.payload["build"]["submitter_account_id"] == absorbed_id
        assert entry.after.payload["build"]["submitter_account_id"] == survivor_id


async def test_state_references_cannot_cross_builds(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    first = await seed(sessions)
    second = await seed(sessions)
    async with sessions() as session:
        state_id = await session.scalar(select(BuildHistoryState.id).where(BuildHistoryState.build_id == first))
        statement = text("""
            INSERT INTO build_edit_history
                (build_id, operation_id, operation, source, actor_system, after_state_id)
            VALUES (:build, gen_random_uuid(), 'create', 'test', 'test', :state)
        """)
        with pytest.raises(DBAPIError, match="foreign key"):
            await session.execute(statement, {"build": second, "state": state_id})


async def test_rollout_seeds_one_baseline_per_existing_build(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_id = await seed(sessions)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.downgrade(config, "c9f4e7a2b5d8")
    command.upgrade(config, "head")
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 1
        assert page.items[0].change.operation == "baseline"
        assert page.items[0].change.actor_system == "history_baseline"
        entry = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert entry is not None
        assert entry.before is None
        assert entry.after is not None
    await edit(partial(SqlAlchemyUnitOfWork, sessions), build_id, "after rollout")
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BuildHistoryState)) == 2


async def test_timeline_omits_payloads_and_measure_recording_cost(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_id = await seed(sessions)
    statements: list[str] = []

    def capture(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    engine = cast(AsyncEngine, sessions.kw["bind"])
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with sessions.begin() as session, record_build_changes(session, change(), (build_id,)):
            await session.execute(
                text("UPDATE builds SET description = :description WHERE id = :id"),
                {"id": build_id, "description": "description " * 800},
            )
        recording_statements = len(statements)
        statements.clear()
        async with SqlAlchemyUnitOfWork(sessions) as uow:
            page = await uow.build_history.list_history(build_id)
            assert page.total == 2
        assert statements
        assert all("snapshot" not in statement for statement in statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    async with sessions() as session:
        sizes = (
            await session.execute(
                text("""
            SELECT octet_length(snapshot::text), pg_column_size(snapshot)
            FROM build_history_states ORDER BY id
        """)
            )
        ).all()
    print(
        f"History recording: {recording_statements} statements including the mutation; "
        f"snapshot JSON/stored bytes: {[(raw, stored) for raw, stored in sizes]}"
    )
