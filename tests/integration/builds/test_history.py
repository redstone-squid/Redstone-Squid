"""Editing scopes are mandatory; PostgreSQL records their complete transactional outcome."""

from functools import partial

import anyio
import pytest
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from squid.accounts.infrastructure.account_merge import merge_accounts
from squid.accounts.models import Account
from squid.builds.domain import Status
from squid.builds.errors import BuildRevisionMismatchError
from squid.builds.history import BuildEditContext
from squid.builds.history_models import BuildHistoryEntry, BuildHistorySnapshot, PendingBuildEdit
from squid.builds.infrastructure.edit_scope import build_edit_scope
from squid.builds.models import OtherBuild
from squid.core.pagination import PageSelector
from squid.persistence.account_merge import AccountMergeContext
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork


def edit_context() -> BuildEditContext:
    return BuildEditContext(operation="edit", source="test", actor_system="test_worker")


async def seed(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    async with SqlAlchemyUnitOfWork(sessions) as uow, uow.builds.editing(actor="test_worker", source="test"):
        build = OtherBuild(submitter_account_id=account_id, submission_status=Status.PENDING, description="first")
        await uow.builds.save(build)
        await uow.commit()
        assert build.id is not None
        return build.id


async def test_snapshot_reuse_pagination_and_final_state(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    for description in ("second", "third"):
        async with factory() as uow, uow.builds.editing(actor="test_worker", source="test"):
            build = await uow.builds.get_for_update(build_id)
            assert build is not None
            build.description = "intermediate"
            await uow.builds.save(build)
            build.description = description
            # A tracked mutation without save must also be captured, at commit.
            await uow.commit()
    async with sessions() as session:
        entries = (await session.scalars(select(BuildHistoryEntry).order_by(BuildHistoryEntry.id))).all()
        assert len(entries) == 3
        assert await session.scalar(select(func.count()).select_from(BuildHistorySnapshot)) == 3
        assert entries[1].before_state_id == entries[0].after_state_id
        assert entries[2].before_state_id == entries[1].after_state_id
        assert await session.scalar(select(func.count()).select_from(PendingBuildEdit)) == 0
    async with factory() as uow:
        page = await uow.build_history.list_history(build_id, page_size=1)
        assert page.total == 3
        assert page.next is not None
        second = await uow.build_history.list_history(
            build_id, selector=PageSelector(after_id=page.next.after_id), page_size=1
        )
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
        assert isinstance(detail.after.payload["build"], dict)
        assert detail.before.payload["build"]["description"] == "second"
        assert detail.after.payload["build"]["description"] == "third"
        assert await uow.build_history.get_history_entry(build_id + 1, detail.id) is None


@pytest.fixture
async def sessions(migrated_session_factory: async_sessionmaker[AsyncSession]) -> async_sessionmaker[AsyncSession]:
    return migrated_session_factory


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE builds SET description = 'unscoped' WHERE id = :id",
        "INSERT INTO build_links (build_id, url, media_type) VALUES (:id, 'https://example.com', 'image')",
        "DELETE FROM builds WHERE id = :id",
    ],
)
async def test_unscoped_writes_are_rejected(sessions: async_sessionmaker[AsyncSession], statement: str) -> None:
    build_id = await seed(sessions)
    async with sessions() as session:
        with pytest.raises(DBAPIError, match="active build editing scope"):
            await session.execute(text(statement), {"id": build_id})


async def test_scope_closes_flushes_and_does_not_leak(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        async with uow.builds.editing(actor="test_worker", source="test"):
            build = await uow.builds.get_by_id(build_id)
            assert build is not None
            build.description = "flushed on exit"
        await uow.commit()
    async with sessions() as session:
        assert (
            await session.scalar(text("SELECT description FROM builds WHERE id = :id"), {"id": build_id})
            == "flushed on exit"
        )
        with pytest.raises(DBAPIError, match="active build editing scope"):
            await session.execute(text("UPDATE builds SET description = 'leaked' WHERE id = :id"), {"id": build_id})
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        async with uow.builds.editing(actor="test_worker", source="test"):
            pass
        with pytest.raises(DBAPIError, match="Only one"):
            async with uow.builds.editing(actor="test_worker", source="test"):
                pass


async def test_noops_bookkeeping_rollback_and_direct_sql(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    async with sessions.begin() as session:
        await session.execute(
            text("UPDATE builds SET edited_time = now(), revision = revision + 1 WHERE id = :id"), {"id": build_id}
        )
    async with sessions.begin() as session, build_edit_scope(session, edit_context()):
        await session.execute(text("UPDATE builds SET description = 'transient' WHERE id = :id"), {"id": build_id})
        await session.execute(text("UPDATE builds SET description = 'first' WHERE id = :id"), {"id": build_id})
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        with pytest.raises(ValueError, match="cancel"):  # noqa: PT012 -- verify rollback when a scope body fails
            async with uow.builds.editing(actor="test_worker", source="test"):
                build = await uow.builds.get_by_id(build_id)
                assert build is not None
                build.description = "rolled back"
                await uow.builds.save(build)
                raise ValueError("cancel")
    async with sessions.begin() as session, build_edit_scope(session, edit_context()):
        await session.execute(
            text("INSERT INTO build_links (build_id, url, media_type) VALUES (:id, 'https://example.com', 'image')"),
            {"id": build_id},
        )
        await session.execute(text("UPDATE builds SET description = 'second' WHERE id = :id"), {"id": build_id})
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 2
        detail = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert detail is not None
        assert detail.after is not None
        assert detail.after.payload["links"] == [{"url": "https://example.com", "media_type": "image"}]


@pytest.mark.parametrize("table", ["build_history_states", "build_edit_history"])
@pytest.mark.parametrize(
    "statement", ["UPDATE {table} SET build_id = build_id", "DELETE FROM {table}", "TRUNCATE {table} CASCADE"]
)
async def test_immutability(sessions: async_sessionmaker[AsyncSession], table: str, statement: str) -> None:
    await seed(sessions)
    async with sessions() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(text(statement.format(table=table)))


async def test_concurrent_edits_record_only_the_winner(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        original = await uow.builds.get_by_id(build_id)
        assert original is not None
    results: list[str] = []

    async def write(description: str) -> None:
        draft = original.copy_for_edit()
        draft.description = description
        try:
            async with SqlAlchemyUnitOfWork(sessions) as uow, uow.builds.editing(actor="test_worker", source="test"):
                await uow.builds.save(draft)
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


async def test_merge_and_delete_retain_history(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    async with sessions.begin() as session:
        survivor = Account()
        session.add(survivor)
        await session.flush()
        survivor_id = survivor.id
        absorbed = await session.scalar(
            text("SELECT submitter_account_id FROM builds WHERE id = :id"), {"id": build_id}
        )
        assert isinstance(absorbed, int)
    context = BuildEditContext(operation="account_merge", source="merge", actor_account_id=survivor_id)
    async with sessions.begin() as session, build_edit_scope(session, context):
        await merge_accounts(session, AccountMergeContext(survivor_id, absorbed))
    async with sessions.begin() as session, build_edit_scope(session, edit_context()):
        await session.execute(text("DELETE FROM builds WHERE id = :id"), {"id": build_id})
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 3
        assert page.items[0].context.operation == "delete"
        assert page.items[1].context == context


async def test_migration_preserves_history(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    config = Config("alembic.ini", toml_file="pyproject.toml")
    command.downgrade(config, "d0a5f8b3c6e9")
    command.upgrade(config, "head")
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        assert (await uow.build_history.list_history(build_id)).total == 1


async def test_writes_after_scope_exit_roll_back_the_transaction(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    async with sessions() as session:
        async with build_edit_scope(session, edit_context()):
            await session.execute(text("UPDATE builds SET description = 'scoped' WHERE id = :id"), {"id": build_id})
        with pytest.raises(DBAPIError, match="active build editing scope"):
            await session.execute(text("UPDATE builds SET description = 'late' WHERE id = :id"), {"id": build_id})
        await session.rollback()
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BuildHistoryEntry)) == 1
        assert await session.scalar(text("SELECT description FROM builds WHERE id = :id"), {"id": build_id}) == "first"


async def test_savepoint_rollback_keeps_only_surviving_changes(sessions: async_sessionmaker[AsyncSession]) -> None:
    build_id = await seed(sessions)
    async with sessions.begin() as session, build_edit_scope(session, edit_context()):
        savepoint = await session.begin_nested()
        await session.execute(text("UPDATE builds SET description = 'discarded' WHERE id = :id"), {"id": build_id})
        await savepoint.rollback()
        await session.execute(text("UPDATE builds SET description = 'survived' WHERE id = :id"), {"id": build_id})
    async with SqlAlchemyUnitOfWork(sessions) as uow:
        page = await uow.build_history.list_history(build_id)
        assert page.total == 2
        detail = await uow.build_history.get_history_entry(build_id, page.items[0].id)
        assert detail is not None
        assert detail.before is not None
        assert detail.after is not None
        assert isinstance(detail.before.payload["build"], dict)
        assert isinstance(detail.after.payload["build"], dict)
        assert detail.before.payload["build"]["description"] == "first"
        assert detail.after.payload["build"]["description"] == "survived"
