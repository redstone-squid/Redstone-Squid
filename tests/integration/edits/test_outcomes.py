"""Outcome records commit atomically and fencing waits for an in-flight operation."""

from uuid import uuid4

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.edits.domain import EditOutcome, EditTarget, Revision
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork


async def test_outcome_rollback_and_retention(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    outcome = EditOutcome(
        uuid4(), "account:1", b"a" * 32, (Revision(EditTarget("example", "1"), 2),), changed=True, final=False
    )
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
        await work.edits.lock_operation(outcome.operation_id)
        await work.edits.record(outcome)
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
        assert await work.edits.outcome(outcome.operation_id) is None
        await work.edits.lock_operation(outcome.operation_id)
        await work.edits.record(outcome)
        await work.commit()
    async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
        assert await work.edits.outcome(outcome.operation_id) == outcome
        assert await work.edits.purge_expired(now=Instant.now()) == 0
        assert await work.edits.purge_expired(now=Instant.now().add(hours=25)) == 1
        await work.commit()


async def test_reconciliation_waits_for_the_original_commit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    outcome = EditOutcome(uuid4(), "account:1", b"b" * 32, (), changed=False, final=False)
    locked = anyio.Event()
    checking = anyio.Event()

    async def original() -> None:
        async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
            await work.edits.lock_operation(outcome.operation_id)
            locked.set()
            await checking.wait()
            await work.edits.record(outcome)
            await work.commit()

    async def reconcile() -> None:
        await locked.wait()
        async with SqlAlchemyUnitOfWork(migrated_session_factory) as work:
            checking.set()
            await work.edits.lock_operation(outcome.operation_id)
            assert await work.edits.outcome(outcome.operation_id) == outcome

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(original)
            tasks.start_soon(reconcile)
