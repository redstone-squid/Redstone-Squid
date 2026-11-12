"""Idempotency reservation orchestration."""

from collections.abc import Callable
from typing import Protocol

from whenever import Instant

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.core.replay import REPLAY_RETENTION_HOURS
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

    async def complete(self, request: PendingRequest, response: StoredResponse, *, now: Instant) -> StoredResponse: ...

    async def purge_expired(self, *, now: Instant) -> int: ...


class IdempotencyService:
    """Reserve request keys and return stored responses for equivalent retries.

    The API supplies a caller namespace, a client-chosen key, and a fingerprint
    of the request. The caller/key pair identifies the operation being retried;
    the fingerprint checks that the retry still asks for the same thing. A new
    reservation returns PendingRequest so the API can run the handler. A completed
    reservation returns StoredResponse so the API can replay it without running
    the handler again.

    Reserving a key and storing its response use separate transactions. The caller
    runs the operation between them. Ordinary unfinished reservations block retries
    until expiry. A handler with an atomic edit outcome may explicitly resume the
    original mutation identity; its executor must serialize and reconcile writes.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[IdempotencyUnitOfWork],
        ttl_hours: int = REPLAY_RETENTION_HOURS,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        if ttl_hours < 1:
            msg = tr(t"idempotency-idempotency-retention = Idempotency retention must be at least one hour.")
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
        recoverable: bool = False,
    ) -> PendingRequest | StoredResponse:
        """Reserve a caller's key or return its completed response.

        The retention window starts at reservation time and is not extended by
        retries. Reusing a live key with another fingerprint raises
        IdempotencyConflictError. An equivalent unfinished request raises
        IdempotencyInProgressError unless the handler opts into atomic outcome
        recovery. That opt-in returns the original mutation ID, never a new one.
        """
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
            if recoverable and reservation.pending is not None:
                return reservation.pending
            raise IdempotencyInProgressError
        return reservation.response

    async def complete(self, request: PendingRequest, response: StoredResponse) -> StoredResponse:
        """Return the first completed response, retaining it for later equivalent requests."""
        async with self._uow_factory() as uow:
            stored = await uow.idempotency.complete(request, response, now=self._now())
            await uow.commit()
            return stored

    async def purge_expired(self) -> int:
        """Remove expired reservations and responses, returning the number deleted."""
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
