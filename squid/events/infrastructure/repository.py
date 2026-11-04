"""PostgreSQL domain-event delivery adapter."""

from collections.abc import Callable

from sqlalchemy import ColumnElement, join, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.events.application import DomainEventDelivery
from squid.events.models import DomainEvent, DomainEventConsumer, DomainEventDeliveryRecord
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
"""Queue mapping for per-consumer event deliveries.

Health metrics include every registered consumer, even those with no pending
rows. Claim counts track worker takeovers separately from recorded failures.
"""


class PostgresDomainEventRepository:
    """Lease event deliveries in the caller's transaction.

    Each claim receives a new token. Acknowledgements must match that token so a
    worker whose lease was reclaimed cannot remove or update the new claim.
    The caller must commit changes made by these methods.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._queue = ClaimedRowQueue(DOMAIN_EVENT_DELIVERY_SPEC)

    async def claim(self, *, consumer: str, limit: int) -> tuple[DomainEventDelivery, ...]:
        session = self._session()
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
                    select(DomainEvent).where(DomainEvent.id.in_([delivery.event_id for delivery in deliveries]))
                )
            ).all()
        }
        return tuple(
            _delivery(delivery, event)
            for delivery in deliveries
            if (event := events.get(delivery.event_id)) is not None
        )

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        self._session()
        if delivery.claim_token is None:
            return False
        outcome = await self._queue.complete(_identity(delivery), delivery.claim_token, session=self._session())
        return outcome.applied

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        self._session()
        if delivery.claim_token is None:
            return False
        outcome = await self._queue.fail(
            _identity(delivery),
            delivery.claim_token,
            attempts=delivery.attempts,
            error=error,
            max_attempts=max_attempts,
            session=self._session(),
        )
        return outcome.dead_lettered

    async def reject(self, delivery: DomainEventDelivery, error: str) -> bool:
        """Dead-letter a delivery this consumer will never accept."""
        self._session()
        if delivery.claim_token is None:
            return False
        outcome = await self._queue.fail(
            _identity(delivery),
            delivery.claim_token,
            attempts=delivery.attempts,
            error=error,
            max_attempts=None,
            terminal=True,
            session=self._session(),
        )
        return outcome.dead_lettered


def _delivery(record: DomainEventDeliveryRecord, event: DomainEvent) -> DomainEventDelivery:
    assert record.claimed_at is not None and record.claim_token is not None
    return DomainEventDelivery(
        event=event,
        consumer=record.consumer,
        attempts=record.attempts,
        claimed_at=record.claimed_at,
        claim_token=record.claim_token,
        claim_count=record.claim_count,
    )


def _identity(delivery: DomainEventDelivery) -> tuple[ColumnElement[bool], ...]:
    """Select one event and consumer; the queue helper separately checks its claim token."""
    return (
        DomainEventDeliveryRecord.event_id == delivery.event.id,
        DomainEventDeliveryRecord.consumer == delivery.consumer,
    )
