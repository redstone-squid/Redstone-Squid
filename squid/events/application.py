"""Application service for durable domain-event delivery."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """One recorded state transition."""

    id: int
    event_type: str
    aggregate_kind: str
    aggregate_id: int
    occurred_at: Instant
    payload: dict[str, object] = field(default_factory=dict)
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class DomainEventDelivery:
    """One claimed delivery of an event to a single consumer."""

    event: DomainEvent
    consumer: str
    attempts: int
    claimed_at: Instant
    claim_token: UUID | None = None
    claim_count: int = 0


class DomainEventRepository(Protocol):
    """Persistence required by the domain-event dispatcher."""

    async def claim(self, *, consumer: str, limit: int) -> Sequence[DomainEventDelivery]: ...

    async def complete(self, delivery: DomainEventDelivery) -> bool: ...

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool: ...

    async def reject(self, delivery: DomainEventDelivery, error: str) -> bool: ...


class UnsupportedEventVersionError(DataIntegrityError):
    """A consumer cannot safely interpret an event envelope version."""


class DomainEventService:
    """Claim and acknowledge domain events on behalf of one named consumer.

    Delivery is at-least-once: a handler that crashes after its side effect but
    before the acknowledgement will see the event again, so handlers must be
    idempotent.
    """

    def __init__(self, repository: DomainEventRepository, *, max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = tr(t"max_attempts must be positive")
            raise InvalidStateError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, consumer: str, limit: int = 20) -> Sequence[DomainEventDelivery]:
        """Claim ready deliveries, reclaiming those abandoned by crashed workers."""
        if not consumer:
            msg = tr(t"consumer must be a non-empty name")
            raise InvalidStateError(msg)
        if not 1 <= limit <= 100:
            msg = tr(t"claim limit must be between 1 and 100")
            raise InvalidStateError(msg)
        return await self._repository.claim(consumer=consumer, limit=limit)

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        """Acknowledge a delivery only if its claim is still current."""
        return await self._repository.complete(delivery)

    async def fail(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        """Retry a failed delivery with backoff, or dead-letter it at the attempt ceiling.

        Returns whether the delivery was dead-lettered.
        """
        return await self._repository.fail(delivery, str(error)[:4000], max_attempts=self._max_attempts)

    async def reject(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        """Permanently reject an event whose contract cannot be interpreted."""
        return await self._repository.reject(delivery, str(error)[:4000])
