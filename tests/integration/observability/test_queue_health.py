"""Queue-health labels and claim/backoff counts against the migrated schema."""

from functools import partial

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.worker.queue_health import QUEUE_HEALTH_STATEMENT
from tests.support.unit_of_work import transact

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


async def _health(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Row[str, int, int, int, int]]:
    async with session_factory() as session:
        return {row.queue: row for row in (await session.execute(QUEUE_HEALTH_STATEMENT)).all()}


async def test_every_queue_reports_including_consumers_with_no_outstanding_work(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A registered consumer with an empty delivery table still reports zero."""
    health = await _health(migrated_session_factory)
    assert set(health) == EXPECTED_QUEUES
    # This queue has no dead-letter state but must still report the same series.
    assert health["record_recomputation"].dead_letters == 0


async def test_a_claim_moves_a_queue_from_ready_to_in_flight(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', '1')"))
    before = (await _health(migrated_session_factory))["discord_sync"]
    assert (before.ready, before.in_flight) == (1, 0)

    await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.reconciliation.claim(limit=10),
        commit=True,
    )

    after = (await _health(migrated_session_factory))["discord_sync"]
    assert (after.ready, after.in_flight) == (0, 1)


async def test_a_backed_off_row_is_neither_ready_nor_in_flight(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A retry waiting for its availability deadline is neither ready, in flight, nor dead-lettered."""
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', '1')"))
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (job,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    await transact(queue, lambda uow: uow.reconciliation.fail(job, "boom", max_attempts=8), commit=True)

    row = (await _health(migrated_session_factory))["discord_sync"]
    assert (row.ready, row.in_flight, row.dead_letters) == (0, 0, 0)
