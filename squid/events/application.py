"""Application service for durable domain-event delivery."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from whenever import Instant


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """One recorded state transition."""

    id: int
    event_type: str
    aggregate_kind: str
    aggregate_id: int
    occurred_at: Instant
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainEventDelivery:
    """One claimed delivery of an event to a single consumer."""

    event: DomainEvent
    consumer: str
    attempts: int
    claimed_at: Instant


class DomainEventRepository(Protocol):
    """Persistence required by the domain-event dispatcher."""

    async def claim(self, *, consumer: str, limit: int) -> Sequence[DomainEventDelivery]: ...

    async def complete(self, delivery: DomainEventDelivery) -> bool: ...

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool: ...


class DomainEventService:
    """Claim and acknowledge domain events on behalf of one named consumer.

    Delivery is at-least-once: a handler that crashes after its side effect but
    before the acknowledgement will see the event again, so handlers must be
    idempotent.
    """

    def __init__(self, repository: DomainEventRepository, *, max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, consumer: str, limit: int = 20) -> Sequence[DomainEventDelivery]:
        """Claim ready deliveries, reclaiming those abandoned by crashed workers."""
        if not consumer:
            msg = "consumer must be a non-empty name"
            raise ValueError(msg)
        if not 1 <= limit <= 100:
            msg = "claim limit must be between 1 and 100"
            raise ValueError(msg)
        return await self._repository.claim(consumer=consumer, limit=limit)

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        """Acknowledge a delivery only if its claim is still current."""
        return await self._repository.complete(delivery)

    async def fail(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        """Retry a failed delivery with backoff, or dead-letter it at the attempt ceiling.

        Returns whether the delivery was dead-lettered.
        """
        return await self._repository.fail(delivery, str(error)[:4000], max_attempts=self._max_attempts)
