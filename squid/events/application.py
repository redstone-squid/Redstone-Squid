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
    """One recorded state transition; `schema_version` tells a consumer how to read `payload`."""

    id: int
    event_type: str
    aggregate_kind: str
    aggregate_id: int
    occurred_at: Instant
    payload: dict[str, object] = field(default_factory=dict)
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class DomainEventDelivery:
    """One claimed delivery of an event to a single consumer; `claim_token` fences its acknowledgement."""

    event: DomainEvent
    consumer: str
    attempts: int
    claimed_at: Instant
    claim_token: UUID | None = None
    claim_count: int = 0


class DomainEventRepository(Protocol):
    """Persistence required by the domain-event dispatcher.

    Every acknowledgement is fenced on the delivery's claim token: one whose claim has expired and been taken by
    another worker applies nothing and reports `False`.
    """

    async def claim(self, *, consumer: str, limit: int) -> Sequence[DomainEventDelivery]:
        """Claim up to `limit` of the consumer's ready deliveries, including ones whose earlier claim expired."""
        ...

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        """Acknowledge the delivery, returning whether the claim was still current."""
        ...

    async def fail(self, delivery: DomainEventDelivery, error: str, *, max_attempts: int) -> bool:
        """Release the delivery for a later attempt with backoff, recording `error`.

        Returns whether this failure dead-lettered it by reaching `max_attempts`.
        """
        ...

    async def reject(self, delivery: DomainEventDelivery, error: str) -> bool:
        """Dead-letter the delivery now, whatever its attempt count, returning whether the claim was current."""
        ...


class UnsupportedEventVersionError(DataIntegrityError):
    """A consumer cannot interpret an event's schema version.

    Raising it from a handler rejects the delivery outright, since a retry would read the same envelope again.
    """


class DomainEventService:
    """Claim and acknowledge domain events on behalf of one named consumer.

    Delivery is at-least-once: a handler that crashes after its side effect but before the acknowledgement sees
    the event again, so handlers must be idempotent. Construction raises `InvalidStateError` unless `max_attempts`
    is positive.
    """

    def __init__(self, repository: DomainEventRepository, *, max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = tr(t"max_attempts must be positive")
            raise InvalidStateError(msg)
        self._repository = repository
        self._max_attempts = max_attempts

    async def claim(self, consumer: str, limit: int = 20) -> Sequence[DomainEventDelivery]:
        """Claim ready deliveries, reclaiming those abandoned by crashed workers.

        Raises `InvalidStateError` on an empty consumer name or a `limit` outside 1-100.
        """
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
        """Dead-letter an event whose contract cannot be interpreted, bypassing the attempt ceiling."""
        return await self._repository.reject(delivery, str(error)[:4000])
