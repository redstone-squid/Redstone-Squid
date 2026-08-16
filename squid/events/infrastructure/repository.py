"""PostgreSQL domain-event delivery adapter."""

from sqlalchemy import ColumnElement, join, literal, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.events.application import DomainEvent, DomainEventDelivery
from squid.events.infrastructure.models import DomainEventConsumer, DomainEventDeliveryRecord, DomainEventRecord
from squid.persistence.queue import ClaimedRowQueue, QueueHealthShape, QueueSpec

DOMAIN_EVENT_DELIVERY_SPEC = QueueSpec(
    name="domain_events",
    model=DomainEventDeliveryRecord,
    key=(DomainEventDeliveryRecord.event_id, DomainEventDeliveryRecord.consumer),
    available_at=DomainEventDeliveryRecord.available_at,
    claimed_at=DomainEventDeliveryRecord.claimed_at,
    claim_token=DomainEventDeliveryRecord.claim_token,
    attempts=DomainEventDeliveryRecord.attempts,
    last_error=DomainEventDeliveryRecord.last_error,
    dead_at=DomainEventDeliveryRecord.dead_at,
    claim_count=DomainEventDeliveryRecord.claim_count,
    health=QueueHealthShape(
        # Deliveries are counted per registered consumer through an outer join, so a
        # consumer with no outstanding rows reports zero rather than disappearing
        # from the metric entirely.
        label=literal("domain_events.").concat(DomainEventConsumer.name),
        source=join(
            DomainEventConsumer,
            DomainEventDeliveryRecord,
            DomainEventDeliveryRecord.consumer == DomainEventConsumer.name,
            isouter=True,
        ),
        group_by=(DomainEventConsumer.name,),
        counted=DomainEventDeliveryRecord.event_id,
    ),
)
"""The queue the shared protocol was modelled on.

It converts with no new configuration knobs beyond `claim_count`, which it already
had, and the four-field health shape below -- which exists because this is the one
queue whose gauges are keyed by something other than the table.
"""


class PostgresDomainEventRepository:
    """Claim per-consumer deliveries with database-clock UUID fencing tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(DOMAIN_EVENT_DELIVERY_SPEC, session_factory)

    async def claim(self, *, consumer: str, limit: int) -> tuple[DomainEventDelivery, ...]:
        async with self._session_factory() as session, session.begin():
            deliveries = await self._queue.claim(
                limit=limit,
                where=(DomainEventDeliveryRecord.consumer == consumer,),
                session=session,
            )
            if not deliveries:
                return ()
            events = {
                event.id: event
                for event in (
                    await session.scalars(
                        select(DomainEventRecord).where(
                            DomainEventRecord.id.in_([delivery.event_id for delivery in deliveries])
                        )
                    )
                ).all()
            }
            return tuple(
                _delivery(delivery, event)
                for delivery in deliveries
                if (event := events.get(delivery.event_id)) is not None
            )

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        if delivery.claim_token is None:
            return False
        outcome = await self._queue.complete(_identity(delivery), delivery.claim_token)
        return outcome.applied

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        if delivery.claim_token is None:
            return False
        outcome = await self._queue.fail(
            _identity(delivery),
            delivery.claim_token,
            attempts=delivery.attempts,
            error=error,
            max_attempts=max_attempts,
        )
        return outcome.dead_lettered

    async def reject(self, delivery: DomainEventDelivery, error: str) -> bool:
        """Dead-letter a delivery this consumer will never accept."""
        if delivery.claim_token is None:
            return False
        outcome = await self._queue.fail(
            _identity(delivery),
            delivery.claim_token,
            attempts=delivery.attempts,
            error=error,
            max_attempts=None,
            terminal=True,
        )
        return outcome.dead_lettered


def _delivery(record: DomainEventDeliveryRecord, event: DomainEventRecord) -> DomainEventDelivery:
    assert record.claimed_at is not None and record.claim_token is not None
    return DomainEventDelivery(
        event=DomainEvent(
            id=event.id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            aggregate_kind=event.aggregate_kind,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
        ),
        consumer=record.consumer,
        attempts=record.attempts,
        claimed_at=record.claimed_at,
        claim_token=record.claim_token,
        claim_count=record.claim_count,
    )


def _identity(delivery: DomainEventDelivery) -> tuple[ColumnElement[bool], ...]:
    """Name one delivery row. The fence on top of it is the shared helper's job."""
    return (
        DomainEventDeliveryRecord.event_id == delivery.event.id,
        DomainEventDeliveryRecord.consumer == delivery.consumer,
    )
