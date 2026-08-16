"""PostgreSQL domain-event delivery adapter."""

from sqlalchemy import ColumnElement, delete, func, join, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.events.application import DomainEvent, DomainEventDelivery
from squid.events.infrastructure.models import DomainEventConsumer, DomainEventDeliveryRecord, DomainEventRecord
from squid.persistence.queue import VISIBILITY_TIMEOUT, QueueHealthShape, QueueSpec, retry_delay

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

    async def claim(self, *, consumer: str, limit: int) -> tuple[DomainEventDelivery, ...]:
        async with self._session_factory() as session, session.begin():
            event_ids = tuple(
                (
                    await session.execute(
                        select(DomainEventDeliveryRecord.event_id)
                        .where(
                            DomainEventDeliveryRecord.consumer == consumer,
                            DomainEventDeliveryRecord.available_at <= func.now(),
                            DomainEventDeliveryRecord.dead_at.is_(None),
                            or_(
                                DomainEventDeliveryRecord.claimed_at.is_(None),
                                DomainEventDeliveryRecord.claimed_at < func.now() - VISIBILITY_TIMEOUT,
                            ),
                        )
                        .order_by(DomainEventDeliveryRecord.event_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            if not event_ids:
                return ()
            claimed = (
                await session.execute(
                    update(DomainEventDeliveryRecord)
                    .where(
                        DomainEventDeliveryRecord.consumer == consumer,
                        DomainEventDeliveryRecord.event_id.in_(event_ids),
                    )
                    .values(
                        claimed_at=func.now(),
                        claim_token=func.gen_random_uuid(),
                        claim_count=DomainEventDeliveryRecord.claim_count + 1,
                    )
                    .returning(DomainEventDeliveryRecord)
                )
            ).scalars()
            delivery_by_event = {delivery.event_id: delivery for delivery in claimed}
            events = (
                await session.execute(
                    select(DomainEventRecord).where(DomainEventRecord.id.in_(event_ids)).order_by(DomainEventRecord.id)
                )
            ).scalars()
            return tuple(
                _delivery(delivery_by_event[event.id], event) for event in events if event.id in delivery_by_event
            )

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        if delivery.claim_token is None:
            return False
        async with self._session_factory() as session, session.begin():
            deleted = await session.scalar(
                delete(DomainEventDeliveryRecord).where(*_claim(delivery)).returning(DomainEventDeliveryRecord.event_id)
            )
            return deleted is not None

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        if delivery.claim_token is None:
            return False
        attempts = delivery.attempts + 1
        async with self._session_factory() as session, session.begin():
            if attempts >= max_attempts:
                dead = await session.scalar(
                    update(DomainEventDeliveryRecord)
                    .where(*_claim(delivery))
                    .values(
                        attempts=attempts,
                        claimed_at=None,
                        claim_token=None,
                        last_error=error[:4000],
                        dead_at=func.now(),
                    )
                    .returning(DomainEventDeliveryRecord.event_id)
                )
                return dead is not None
            await session.execute(
                update(DomainEventDeliveryRecord)
                .where(*_claim(delivery))
                .values(
                    attempts=attempts,
                    claimed_at=None,
                    claim_token=None,
                    last_error=error[:4000],
                    available_at=func.now() + retry_delay(attempts),
                )
            )
            return False

    async def reject(self, delivery: DomainEventDelivery, error: str) -> bool:
        if delivery.claim_token is None:
            return False
        async with self._session_factory() as session, session.begin():
            dead = await session.scalar(
                update(DomainEventDeliveryRecord)
                .where(*_claim(delivery))
                .values(
                    attempts=delivery.attempts + 1,
                    claimed_at=None,
                    claim_token=None,
                    last_error=error[:4000],
                    dead_at=func.now(),
                )
                .returning(DomainEventDeliveryRecord.event_id)
            )
            return dead is not None


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


def _claim(delivery: DomainEventDelivery) -> tuple[ColumnElement[bool], ...]:
    return (
        DomainEventDeliveryRecord.event_id == delivery.event.id,
        DomainEventDeliveryRecord.consumer == delivery.consumer,
        DomainEventDeliveryRecord.claim_token == delivery.claim_token,
    )
