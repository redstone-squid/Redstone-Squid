"""Idempotency reservation orchestration."""

from collections.abc import Callable
from typing import Protocol

from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.idempotency.domain import (
    ExistingRequest,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    PendingRequest,
    Reservation,
    StoredResponse,
    UnsafeHttpMethod,
)
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class IdempotencyRepository(Protocol):
    """Persistence required to reserve and complete caller-scoped keys."""

    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: UnsafeHttpMethod,
        route: str,
        expires_at: Instant,
        now: Instant,
    ) -> Reservation: ...

    async def complete(self, request: PendingRequest, response: StoredResponse, *, now: Instant) -> None: ...

    async def purge_expired(self, *, now: Instant) -> int: ...


class IdempotencyService:
    """Claim caller keys and distinguish replay from conflicting reuse."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[IdempotencyUnitOfWork],
        ttl_hours: int = 24,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        if ttl_hours < 1:
            msg = tr(t"Idempotency retention must be at least one hour.")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._ttl_hours = ttl_hours
        self._now = now

    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: UnsafeHttpMethod,
        route: str,
    ) -> PendingRequest | StoredResponse:
        """Reserve a new key or return its completed response for replay."""
        now = self._now()
        async with self._uow_factory() as uow:
            reservation = await uow.idempotency.reserve(
                caller=caller,
                key=key,
                fingerprint=fingerprint,
                method=method,
                route=route,
                expires_at=now.add(hours=self._ttl_hours),
                now=now,
            )
            await uow.commit()
        if isinstance(reservation, PendingRequest):
            return reservation
        self._validate_existing(reservation, fingerprint)
        if reservation.response is None:
            raise IdempotencyInProgressError
        return reservation.response

    async def complete(self, request: PendingRequest, response: StoredResponse) -> None:
        """Make a buffered response available to later equivalent requests."""
        async with self._uow_factory() as uow:
            await uow.idempotency.complete(request, response, now=self._now())
            await uow.commit()

    async def purge_expired(self) -> int:
        """Remove replay state whose bounded retention window has elapsed."""
        async with self._uow_factory() as uow:
            result = await uow.idempotency.purge_expired(now=self._now())
            await uow.commit()
            return result

    @staticmethod
    def _validate_existing(existing: ExistingRequest, fingerprint: bytes) -> None:
        if existing.fingerprint != fingerprint:
            raise IdempotencyConflictError


class IdempotencyUnitOfWork(UnitOfWork, Protocol):
    @property
    def idempotency(self) -> IdempotencyRepository: ...
