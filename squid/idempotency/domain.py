"""Idempotency request and response values."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from squid.core.errors import ConflictError, ErrorCode
from squid.core.i18n import tr


class IdempotencyState(StrEnum):
    """Durable lifecycle states for one caller-scoped reservation."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class UnsafeHttpMethod(StrEnum):
    """HTTP methods eligible for durable mutation replay."""

    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass(frozen=True, slots=True)
class PendingRequest:
    """A newly reserved request whose response is not stored yet."""

    request_id: UUID


@dataclass(frozen=True, slots=True)
class StoredResponse:
    """An HTTP response retained for deterministic request replay."""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class ExistingRequest:
    """The state found for an already reserved caller key."""

    fingerprint: bytes
    response: StoredResponse | None


type Reservation = PendingRequest | ExistingRequest


class IdempotencyConflictError(ConflictError):
    """A caller reused one idempotency key for a different request."""

    default_message = tr(t"The idempotency key was already used for a different request.")
    default_title = tr(t"Idempotency key conflict")
    default_code = ErrorCode.IDEMPOTENCY_CONFLICT


class IdempotencyInProgressError(ConflictError):
    """An equivalent request with this caller key has not completed yet."""

    default_message = tr(t"A request with this idempotency key is still in progress.")
    default_title = tr(t"Idempotent request in progress")
    default_code = ErrorCode.IDEMPOTENCY_IN_PROGRESS
