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
)


class IdempotencyRepository(Protocol):
    """Persistence required to reserve and complete caller-scoped keys."""

    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: str,
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
        repository: IdempotencyRepository,
        *,
        ttl_hours: int = 24,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        if ttl_hours < 1:
            msg = tr(t"Idempotency retention must be at least one hour.")
            raise InvalidStateError(msg)
        self._repository = repository
        self._ttl_hours = ttl_hours
        self._now = now

    async def reserve(
        self,
        *,
        caller: str,
        key: str,
        fingerprint: bytes,
        method: str,
        route: str,
    ) -> PendingRequest | StoredResponse:
        """Reserve a new key or return its completed response for replay."""
        now = self._now()
        reservation = await self._repository.reserve(
            caller=caller,
            key=key,
            fingerprint=fingerprint,
            method=method,
            route=route,
            expires_at=now.add(hours=self._ttl_hours),
            now=now,
        )
        if isinstance(reservation, PendingRequest):
            return reservation
        self._validate_existing(reservation, fingerprint)
        if reservation.response is None:
            raise IdempotencyInProgressError
        return reservation.response

    async def complete(self, request: PendingRequest, response: StoredResponse) -> None:
        """Make a buffered response available to later equivalent requests."""
        await self._repository.complete(request, response, now=self._now())

    async def purge_expired(self) -> int:
        """Remove replay state whose bounded retention window has elapsed."""
        return await self._repository.purge_expired(now=self._now())

    @staticmethod
    def _validate_existing(existing: ExistingRequest, fingerprint: bytes) -> None:
        if existing.fingerprint != fingerprint:
            raise IdempotencyConflictError
