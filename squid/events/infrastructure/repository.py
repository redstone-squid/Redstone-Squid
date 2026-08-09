"""PostgreSQL domain-event delivery adapter."""

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.events.application import DomainEvent, DomainEventDelivery
from squid.events.infrastructure.models import DomainEventDeliveryRecord, DomainEventRecord
from squid.persistence.queue import ClaimedRowQueue


class PostgresDomainEventRepository:
    """Claim per-consumer event deliveries with crash-safe claim tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(
            session_factory,
            DomainEventDeliveryRecord,
            ready_at=DomainEventDeliveryRecord.available_at,
            claimed_at=DomainEventDeliveryRecord.claimed_at,
            dead_at=DomainEventDeliveryRecord.dead_at,
        )

    async def claim(self, *, consumer: str, limit: int) -> tuple[DomainEventDelivery, ...]:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(DomainEventDeliveryRecord, DomainEventRecord)
                        .join(DomainEventRecord, DomainEventRecord.id == DomainEventDeliveryRecord.event_id)
                        .where(
                            DomainEventDeliveryRecord.consumer == consumer,
                            DomainEventDeliveryRecord.available_at <= func.now(),
                            self._queue.reclaimable(),
                        )
                        # Ordered by event id so a consumer observes transitions on one
                        # aggregate in the order they actually happened.
                        .order_by(DomainEventDeliveryRecord.event_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True, of=DomainEventDeliveryRecord)
                    )
                ).all()
            )
            claimed_at = await self._queue.stamp(tuple(delivery for delivery, _ in rows), session)
            return tuple(
                DomainEventDelivery(
                    event=DomainEvent(
                        id=event.id,
                        event_type=event.event_type,
                        aggregate_kind=event.aggregate_kind,
                        aggregate_id=event.aggregate_id,
                        occurred_at=event.occurred_at,
                        payload=dict(event.payload),
                    ),
                    consumer=delivery.consumer,
                    attempts=delivery.attempts,
                    claimed_at=claimed_at,
                )
                for delivery, event in rows
            )

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        return await self._queue.complete(self._identity(delivery), delivery.claimed_at)

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        return await self._queue.fail(
            self._identity(delivery),
            delivery.claimed_at,
            attempts=delivery.attempts,
            error=error,
            max_attempts=max_attempts,
        )

    @staticmethod
    def _identity(delivery: DomainEventDelivery) -> tuple[ColumnElement[bool], ...]:
        return (
            DomainEventDeliveryRecord.event_id == delivery.event.id,
            DomainEventDeliveryRecord.consumer == delivery.consumer,
        )
