"""Record recomputation leases protect each claimed scope with a token.

Re-enqueueing clears the old claim so new work can be leased without allowing an earlier
worker to acknowledge it. Expired claims become available again."""

from functools import partial
from typing import Any

import pytest
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.domain import BuildKind
from tests.support.unit_of_work import transact


@pytest.fixture(autouse=True)
async def empty_queue(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Remove the door and extender rebuilds seeded by migrations so claims cover only test data."""
    async with migrated_session_factory.begin() as session:
        await session.execute(text("DELETE FROM record_recompute_queue"))


async def _enqueue(session_factory: async_sessionmaker[AsyncSession], scope_key: str, kind: str = "door") -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text(
                "INSERT INTO record_recompute_queue (scope_key, build_kind, reasons) "
                "VALUES (:scope, :kind, '[\"source_change\"]'::jsonb) "
                "ON CONFLICT (scope_key) DO UPDATE "
                "SET enqueued_at = now(), available_at = now(), locked_at = NULL, claim_token = NULL"
            ),
            {"scope": scope_key, "kind": kind},
        )


async def _count(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return (await session.execute(text("SELECT count(*) FROM record_recompute_queue"))).scalar_one()


async def test_a_scope_lease_cannot_acknowledge_work_enqueued_during_the_run(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A replacement claim must survive acknowledgement by the worker that held the earlier token."""
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    worker_a = await transact(repository, lambda uow: uow.records.claim_recompute_kinds(limit=10), commit=True)
    assert worker_a.kinds == (BuildKind.DOOR,)

    # New DOOR work arrives mid-run and clears the lock, exactly as the trigger does.
    await _enqueue(migrated_session_factory, "door")
    worker_b = await transact(repository, lambda uow: uow.records.claim_recompute_kinds(limit=10), commit=True)
    assert worker_b.kinds == (BuildKind.DOOR,)
    assert worker_b.claim_tokens != worker_a.claim_tokens

    await transact(repository, lambda uow: uow.records.complete_recompute(worker_a), commit=True)

    assert await _count(migrated_session_factory) == 1
    async with migrated_session_factory() as session:
        still_leased = (await session.execute(text("SELECT claim_token FROM record_recompute_queue"))).scalar_one()
    assert still_leased == worker_b.claim_tokens[0]


async def test_a_crashed_workers_lease_is_reclaimed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    abandoned = await transact(repository, lambda uow: uow.records.claim_recompute_kinds(limit=10), commit=True)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE record_recompute_queue SET locked_at = now() - interval '6 minutes'"))

    reclaimed = await transact(repository, lambda uow: uow.records.claim_recompute_kinds(limit=10), commit=True)
    assert reclaimed.kinds == (BuildKind.DOOR,)
    assert reclaimed.claim_tokens != abandoned.claim_tokens


async def test_a_failed_lease_backs_off_and_stays_queued(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Recomputation has no retry ceiling, so failures remain queued after backoff."""
    await _enqueue(migrated_session_factory, "door")
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    lease = await transact(repository, lambda uow: uow.records.claim_recompute_kinds(limit=10), commit=True)

    await transact(repository, lambda uow: uow.records.fail_recompute(lease, "boom"), commit=True)

    row = await _one(migrated_session_factory)
    assert row == (1, None, None, True, "boom")
    # Backed off, not dropped, and not claimable until the delay elapses.
    assert (
        await transact(repository, lambda uow: uow.records.claim_recompute_kinds(limit=10), commit=True)
    ).kinds == ()
    assert await _count(migrated_session_factory) == 1


async def _one(session_factory: async_sessionmaker[AsyncSession]) -> Row[Any]:
    async with session_factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT attempts, locked_at, claim_token, available_at > now(), last_error "
                    "FROM record_recompute_queue"
                )
            )
        ).one()
