"""Fencing for the record recomputation lease.

This queue leases whole scopes rather than rows and acknowledges by scope, which
was the stated reason it could not be fenced. It stamped `locked_at` per row all
along, so it could carry a token the same way -- and without one it lost work.
"""

from typing import Any

import pytest
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.records.domain import BuildKind
from squid.records.infrastructure.repository import PostgresRecordRepository


@pytest.fixture(autouse=True)
async def empty_queue(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Start from an empty queue.

    Migrations seed a door and an extender rebuild, which would otherwise ride
    along in every claim and hide which rows a lease actually covers.
    """
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
    """Acknowledging by kind silently destroyed work that arrived mid-run.

    `enqueue` upserts on `scope_key` and clears the lock, so new DOOR work arriving
    while worker A rebuilt DOOR was claimed by worker B -- and then deleted by A's
    `WHERE build_kind IN (...) AND locked_at IS NOT NULL`. The recomputation was
    dropped and never ran.
    """
    await _enqueue(migrated_session_factory, "door")
    repository = PostgresRecordRepository(migrated_session_factory)

    worker_a = await repository.claim_recompute_kinds(limit=10)
    assert worker_a.kinds == (BuildKind.DOOR,)

    # New DOOR work arrives mid-run and clears the lock, exactly as the trigger does.
    await _enqueue(migrated_session_factory, "door")
    worker_b = await repository.claim_recompute_kinds(limit=10)
    assert worker_b.kinds == (BuildKind.DOOR,)
    assert worker_b.claim_tokens != worker_a.claim_tokens

    await repository.complete_recompute(worker_a)

    assert await _count(migrated_session_factory) == 1
    async with migrated_session_factory() as session:
        still_leased = (await session.execute(text("SELECT claim_token FROM record_recompute_queue"))).scalar_one()
    assert still_leased == worker_b.claim_tokens[0]


async def test_a_crashed_workers_lease_is_reclaimed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The claim filtered `locked_at IS NULL` with no visibility timeout at all.

    A row locked by a killed worker was never handed to anyone again, and
    `squid/worker/queue_health.py` reported it as in-flight forever.
    """
    await _enqueue(migrated_session_factory, "door")
    repository = PostgresRecordRepository(migrated_session_factory)
    abandoned = await repository.claim_recompute_kinds(limit=10)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE record_recompute_queue SET locked_at = now() - interval '6 minutes'"))

    reclaimed = await repository.claim_recompute_kinds(limit=10)
    assert reclaimed.kinds == (BuildKind.DOOR,)
    assert reclaimed.claim_tokens != abandoned.claim_tokens


async def test_a_failed_lease_backs_off_and_stays_queued(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`max_attempts=None`: a stuck recomputation stays visible rather than vanishing."""
    await _enqueue(migrated_session_factory, "door")
    repository = PostgresRecordRepository(migrated_session_factory)
    lease = await repository.claim_recompute_kinds(limit=10)

    await repository.fail_recompute(lease, "boom")

    row = await _one(migrated_session_factory)
    assert row == (1, None, None, True, "boom")
    # Backed off, not dropped, and not claimable until the delay elapses.
    assert (await repository.claim_recompute_kinds(limit=10)).kinds == ()
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
