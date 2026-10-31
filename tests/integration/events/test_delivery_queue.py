"""Claim, fencing, and backoff semantics of the domain-event delivery queue.

These moved out of `tests/integration/persistence/test_claimed_row_queue.py`, which
they outgrew: `PostgresDomainEventRepository` stopped using `ClaimedRowQueue` at
commit `72cca02` and has minted its own database-side UUID tokens ever since.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.events.application import DomainEventDelivery
from squid.events.infrastructure.repository import PostgresDomainEventRepository


async def _seed_delivery(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO domain_event_consumers (name) VALUES ('discord') ON CONFLICT DO NOTHING")
        )
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


async def test_a_delivery_is_claimed_with_its_event_and_acknowledged(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed_delivery(migrated_session_factory)
    repository = PostgresDomainEventRepository(migrated_session_factory)

    (delivery,) = await repository.claim(consumer="discord", limit=10)
    assert (delivery.event.id, delivery.event.event_type, delivery.consumer) == (event_id, "build.confirmed", "discord")
    assert delivery.claim_token is not None
    assert delivery.claim_count == 1
    assert await repository.complete(delivery) is True
    assert await _count(migrated_session_factory, "domain_event_deliveries") == 0
    # The event itself is a log and outlives its deliveries.
    assert await _count(migrated_session_factory, "domain_events") == 1


async def test_another_consumer_does_not_see_this_consumers_deliveries(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(migrated_session_factory)
    repository = PostgresDomainEventRepository(migrated_session_factory)

    assert await repository.claim(consumer="webhooks", limit=10) == ()
    assert len(await repository.claim(consumer="discord", limit=10)) == 1


async def test_a_stale_delivery_token_cannot_acknowledge(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(migrated_session_factory)
    repository = PostgresDomainEventRepository(migrated_session_factory)
    (delivery,) = await repository.claim(consumer="discord", limit=10)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE domain_event_deliveries SET claimed_at = NULL, claim_token = NULL"))

    assert await repository.complete(delivery) is False
    assert await _count(migrated_session_factory, "domain_event_deliveries") == 1


async def test_a_failed_delivery_backs_off_and_a_ceiling_failure_dead_letters_it(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_delivery(migrated_session_factory)
    repository = PostgresDomainEventRepository(migrated_session_factory)
    (delivery,) = await repository.claim(consumer="discord", limit=10)

    assert await repository.fail(delivery, "boom", max_attempts=8) is False
    async with migrated_session_factory() as session:
        attempts, future = (
            await session.execute(text("SELECT attempts, available_at > now() FROM domain_event_deliveries"))
        ).one()
    assert (attempts, future) == (1, True)

    async with migrated_session_factory.begin() as session:
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
    async with migrated_session_factory() as session:
        attempts, claimed_at, dead, error = (
            await session.execute(
                text("SELECT attempts, claimed_at, dead_at IS NOT NULL, last_error FROM domain_event_deliveries")
            )
        ).one()
    assert (attempts, claimed_at, dead, error) == (8, None, True, "boom")
    assert await repository.claim(consumer="discord", limit=10) == ()
