"""Claim, fencing, and backoff semantics shared by the outbox queues."""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.events.application import DomainEventDelivery
from squid.events.infrastructure.repository import PostgresDomainEventRepository
from squid.sync.application import ReconciliationJob
from squid.sync.infrastructure.repository import PostgresDiscordSyncQueue

_CREATE_SCHEMA = """
CREATE TABLE discord_sync_queue (
    id BIGSERIAL PRIMARY KEY,
    resource_kind TEXT NOT NULL,
    source_key TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'refresh',
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    dead_at TIMESTAMPTZ,
    generation BIGINT NOT NULL DEFAULT 1,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE (resource_kind, source_key)
);
CREATE TABLE domain_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    schema_version SMALLINT NOT NULL DEFAULT 1,
    aggregate_kind TEXT NOT NULL,
    aggregate_id BIGINT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE domain_event_consumers (name TEXT PRIMARY KEY);
CREATE TABLE domain_event_deliveries (
    event_id BIGINT REFERENCES domain_events(id) ON DELETE CASCADE,
    consumer TEXT REFERENCES domain_event_consumers(name) ON DELETE CASCADE,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    claim_token UUID,
    claim_count INTEGER NOT NULL DEFAULT 0,
    dead_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    CHECK (claim_count >= 0),
    CHECK ((claimed_at IS NULL) = (claim_token IS NULL)),
    PRIMARY KEY (event_id, consumer)
);
INSERT INTO domain_event_consumers (name) VALUES ('discord');
"""

_DROP_SCHEMA = """
DROP TABLE IF EXISTS
    domain_event_deliveries, domain_event_consumers, domain_events, discord_sync_queue CASCADE;
"""


@pytest.fixture(autouse=True)
async def queue_schema(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        for statement in _CREATE_SCHEMA.strip().split(";"):
            if statement.strip():
                await connection.execute(text(statement))
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.execute(text(_DROP_SCHEMA))


async def _seed_sync_job(session_factory: async_sessionmaker[AsyncSession], source_key: str = "1") -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', :key)"),
            {"key": source_key},
        )


async def _seed_delivery(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory.begin() as session:
        event_id = (
            await session.execute(
                text(
                    "INSERT INTO domain_events (event_type, aggregate_kind, aggregate_id) "
                    "VALUES ('build.confirmed', 'build', 42) RETURNING id"
                )
            )
        ).scalar_one()
        await session.execute(
            text("INSERT INTO domain_event_deliveries (event_id, consumer) VALUES (:id, 'discord')"),
            {"id": event_id},
        )
        return event_id


async def _count(session_factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with session_factory() as session:
        return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


# --- discord_sync_queue ------------------------------------------------------


async def test_claiming_stamps_a_token_and_completing_removes_the_row(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(async_session_factory)
    queue = PostgresDiscordSyncQueue(async_session_factory)

    (job,) = await queue.claim(limit=10)
    assert job.claimed_at is not None
    assert await queue.complete(job) is True
    assert await _count(async_session_factory, "discord_sync_queue") == 0


async def test_a_stale_claim_token_cannot_delete_a_requeued_row(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A trigger firing mid-job resets claimed_at, which must invalidate the claim."""
    await _seed_sync_job(async_session_factory)
    queue = PostgresDiscordSyncQueue(async_session_factory)
    (job,) = await queue.claim(limit=10)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE discord_sync_queue SET claimed_at = NULL"))

    assert await queue.complete(job) is False
    assert await _count(async_session_factory, "discord_sync_queue") == 1


async def test_a_claimed_row_is_not_handed_to_a_second_worker(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(async_session_factory)
    queue = PostgresDiscordSyncQueue(async_session_factory)

    assert len(await queue.claim(limit=10)) == 1
    assert await queue.claim(limit=10) == ()


async def test_a_claim_abandoned_past_the_visibility_timeout_is_reclaimed(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(async_session_factory)
    queue = PostgresDiscordSyncQueue(async_session_factory)
    await queue.claim(limit=10)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE discord_sync_queue SET claimed_at = now() - interval '6 minutes'"))

    assert len(await queue.claim(limit=10)) == 1


async def test_failing_backs_the_row_off_instead_of_dropping_it(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(async_session_factory)
    queue = PostgresDiscordSyncQueue(async_session_factory)
    (job,) = await queue.claim(limit=10)

    assert await queue.fail(job, "boom", max_attempts=8) is False
    async with async_session_factory() as session:
        attempts, claimed_at, future = (
            await session.execute(text("SELECT attempts, claimed_at, enqueued_at > now() FROM discord_sync_queue"))
        ).one()
    assert (attempts, claimed_at, future) == (1, None, True)
    # Backed-off work is not ready yet, so a drain right now must not pick it up.
    assert await queue.claim(limit=10) == ()


async def test_failing_at_the_attempt_ceiling_dead_letters_the_row(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(async_session_factory)
    queue = PostgresDiscordSyncQueue(async_session_factory)
    (job,) = await queue.claim(limit=10)
    ceiling_job = ReconciliationJob(
        id=job.id,
        resource_kind=job.resource_kind,
        source_key=job.source_key,
        action=job.action,
        generation=job.generation,
        attempts=7,
        claimed_at=job.claimed_at,
    )

    assert await queue.fail(ceiling_job, "boom", max_attempts=8) is True
    async with async_session_factory() as session:
        attempts, claimed_at, dead, error = (
            await session.execute(
                text("SELECT attempts, claimed_at, dead_at IS NOT NULL, last_error FROM discord_sync_queue")
            )
        ).one()
    assert (attempts, claimed_at, dead, error) == (8, None, True, "boom")
    assert await queue.claim(limit=10) == ()


# --- domain_event_deliveries -------------------------------------------------


async def test_a_delivery_is_claimed_with_its_event_and_acknowledged(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed_delivery(async_session_factory)
    repository = PostgresDomainEventRepository(async_session_factory)

    (delivery,) = await repository.claim(consumer="discord", limit=10)
    assert (delivery.event.id, delivery.event.event_type, delivery.consumer) == (event_id, "build.confirmed", "discord")
    assert delivery.claim_token is not None
    assert delivery.claim_count == 1
    assert await repository.complete(delivery) is True
    assert await _count(async_session_factory, "domain_event_deliveries") == 0
    # The event itself is a log and outlives its deliveries.
    assert await _count(async_session_factory, "domain_events") == 1


async def test_another_consumer_does_not_see_this_consumers_deliveries(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    repository = PostgresDomainEventRepository(async_session_factory)

    assert await repository.claim(consumer="webhooks", limit=10) == ()
    assert len(await repository.claim(consumer="discord", limit=10)) == 1


async def test_a_stale_delivery_token_cannot_acknowledge(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    repository = PostgresDomainEventRepository(async_session_factory)
    (delivery,) = await repository.claim(consumer="discord", limit=10)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE domain_event_deliveries SET claimed_at = NULL, claim_token = NULL"))

    assert await repository.complete(delivery) is False
    assert await _count(async_session_factory, "domain_event_deliveries") == 1


async def test_a_failed_delivery_backs_off_and_a_ceiling_failure_dead_letters_it(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(async_session_factory)
    repository = PostgresDomainEventRepository(async_session_factory)
    (delivery,) = await repository.claim(consumer="discord", limit=10)

    assert await repository.fail(delivery, "boom", max_attempts=8) is False
    async with async_session_factory() as session:
        attempts, future = (
            await session.execute(text("SELECT attempts, available_at > now() FROM domain_event_deliveries"))
        ).one()
    assert (attempts, future) == (1, True)

    async with async_session_factory.begin() as session:
        await session.execute(
            text("UPDATE domain_event_deliveries SET available_at = now(), claimed_at = NULL, claim_token = NULL")
        )
    (retried,) = await repository.claim(consumer="discord", limit=10)
    ceiling = DomainEventDelivery(
        event=retried.event,
        consumer=retried.consumer,
        attempts=7,
        claimed_at=retried.claimed_at,
        claim_token=retried.claim_token,
        claim_count=retried.claim_count,
    )
    assert await repository.fail(ceiling, "boom", max_attempts=8) is True
    async with async_session_factory() as session:
        attempts, claimed_at, dead, error = (
            await session.execute(
                text("SELECT attempts, claimed_at, dead_at IS NOT NULL, last_error FROM domain_event_deliveries")
            )
        ).one()
    assert (attempts, claimed_at, dead, error) == (8, None, True, "boom")
    assert await repository.claim(consumer="discord", limit=10) == ()
