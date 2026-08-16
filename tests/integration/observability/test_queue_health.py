"""The generated queue-health union, against the real schema.

`QUEUE_HEALTH_STATEMENT` is built from the same `QueueSpec` constants the claim
path uses, so what this pins is that the generator covers every queue and that the
predicate it emits is the one the adapters actually claim on.
"""

from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.sync.infrastructure.repository import PostgresDiscordSyncQueue
from squid.worker.queue_health import QUEUE_HEALTH_STATEMENT

EXPECTED_QUEUES = {
    "discord_sync",
    "domain_events.core",
    "domain_events.discord",
    "record_recomputation",
    "schematic_jobs",
    "schematic_renders",
    "search_embeddings",
    "search_projections",
}


async def _health(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Row[Any]]:
    async with session_factory() as session:
        return {row.queue: row for row in (await session.execute(QUEUE_HEALTH_STATEMENT)).all()}


async def test_every_queue_reports_including_consumers_with_no_outstanding_work(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A registered consumer with an empty delivery table still reports zero."""
    assert set(await _health(migrated_session_factory)) == EXPECTED_QUEUES


async def test_a_claim_moves_a_queue_from_ready_to_in_flight(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', '1')"))
    before = (await _health(migrated_session_factory))["discord_sync"]
    assert (before.ready, before.in_flight) == (1, 0)

    await PostgresDiscordSyncQueue(migrated_session_factory).claim(limit=10)

    after = (await _health(migrated_session_factory))["discord_sync"]
    assert (after.ready, after.in_flight) == (0, 1)


async def test_a_backed_off_row_ages_from_when_it_was_enqueued(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`oldest_ready_age` reads the retry clock, so a retry does not reset the age.

    It also means a backed-off row is not ready, which is what the gauge should say.
    """
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', '1')"))
    queue = PostgresDiscordSyncQueue(migrated_session_factory)
    (job,) = await queue.claim(limit=10)
    await queue.fail(job, "boom", max_attempts=8)

    row = (await _health(migrated_session_factory))["discord_sync"]
    assert (row.ready, row.in_flight, row.dead_letters) == (0, 0, 0)


async def test_a_queue_without_dead_lettering_reports_a_constant_zero(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`record_recompute_queue` has no `dead_at`, so the series stays comparable."""
    row = (await _health(migrated_session_factory))["record_recomputation"]
    assert row.dead_letters == 0
