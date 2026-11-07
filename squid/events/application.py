"""Application service for durable domain-event delivery."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from whenever import Instant

from squid.core.errors import DataIntegrityError, InvalidStateError
from squid.core.i18n import tr
from squid.events.models import DomainEvent
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class DomainEventDelivery:
    """One claimed delivery of an event to a single consumer."""

    event: DomainEvent
    consumer: str
    attempts: int
    """Failures recorded before this claim; claiming alone does not increment it."""
    claimed_at: Instant
    claim_token: UUID | None = None


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

    A domain event records a state transition, such as a build being confirmed.
    Each registered consumer has its own delivery record, so acknowledging the
    event for one consumer leaves the others' work intact. The dispatcher claims
    deliveries here, runs its handlers, then reports completion or failure.
    Each service call commits before returning; handlers run between transactions.

    Delivery is at-least-once: a handler that crashes after its side effect but
    before the acknowledgement will see the event again, so handlers must be
    idempotent.
    """

    def __init__(self, *, uow_factory: UnitOfWorkFactory[DomainEventUnitOfWork], max_attempts: int = 8) -> None:
        if max_attempts < 1:
            msg = tr(t"max_attempts must be positive")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._max_attempts = max_attempts

    async def claim(self, consumer: str, limit: int = 20) -> Sequence[DomainEventDelivery]:
        """Claim ready deliveries and commit their leases before returning.

        Expired leases can be reclaimed even if the previous worker is still
        running. An empty consumer name or a limit outside 1 to 100 raises
        InvalidStateError.
        """
        async with self._uow_factory() as uow:
            if not consumer:
                msg = tr(t"consumer must be a non-empty name")
                raise InvalidStateError(msg)
            if not 1 <= limit <= 100:
                msg = tr(t"claim limit must be between 1 and 100")
                raise InvalidStateError(msg)
            result = await uow.domain_events.claim(consumer=consumer, limit=limit)
            await uow.commit()
            return result

    async def complete(self, delivery: DomainEventDelivery) -> bool:
        """Remove a delivery and return True only if its claim still matches."""
        async with self._uow_factory() as uow:
            result = await uow.domain_events.complete(delivery)
            await uow.commit()
            return result

    async def fail(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        """Retry a failed delivery with backoff, or dead-letter it at the attempt ceiling.

        Return True if this call stops further retries. False can mean that a
        retry was scheduled or that the claim no longer matches.
        """
        async with self._uow_factory() as uow:
            result = await uow.domain_events.fail(delivery, str(error)[:4000], max_attempts=self._max_attempts)
            await uow.commit()
            return result

    async def reject(self, delivery: DomainEventDelivery, error: Exception) -> bool:
        """Stop retries for an event the consumer cannot interpret.

        Return False if the claim is missing or has been replaced.
        """
        async with self._uow_factory() as uow:
            result = await uow.domain_events.reject(delivery, str(error)[:4000])
            await uow.commit()
            return result


class DomainEventUnitOfWork(UnitOfWork, Protocol):
    @property
    def domain_events(self) -> DomainEventRepository: ...
