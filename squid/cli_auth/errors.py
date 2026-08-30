"""Expected failures from CLI device authorization."""

from collections.abc import Mapping
from typing import ClassVar

from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DomainError,
    JSONValue,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from squid.core.i18n import tr


class CliAuthorizationError(DomainError):
    """Base class for expected CLI authorization failures."""

    code: ClassVar[str] = "cli_authorization_error"  # pyrefly: ignore[bad-override]

    def __init__(
        self,
        message: str | None = None,
        *,
        public_context: Mapping[str, JSONValue] | None = None,
    ) -> None:
        context = {"cli_auth_code": type(self).code, **(public_context or {})}
        super().__init__(message, public_context=context)


class InvalidCliEnrollmentError(CliAuthorizationError, ValidationError):
    """An enrollment code is malformed, unknown, or revoked."""

    code = "invalid_cli_enrollment"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI enrollment is invalid.")


class CliEnrollmentExpiredError(CliAuthorizationError, ConflictError):
    """An enrollment expired before approval or exchange."""

    code = "cli_enrollment_expired"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI enrollment has expired.")


class CliAuthorizationPendingError(CliAuthorizationError, ConflictError):
    """An enrollment has not yet been approved in the browser."""

    code = "cli_authorization_pending"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI enrollment is awaiting browser approval.")


class CliEnrollmentAlreadyExchangedError(CliAuthorizationError, ConflictError):
    """An enrollment has already been exchanged."""

    code = "cli_enrollment_already_exchanged"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI enrollment was already exchanged.")


class CliEnrollmentApprovalDeniedError(CliAuthorizationError, AuthorizationError):
    """An enrollment was already approved by another account."""

    code = "cli_enrollment_approval_denied"  # pyrefly: ignore[bad-override]
    default_message = tr(t"This account cannot approve the CLI enrollment.")


class InvalidCliDeviceProofError(CliAuthorizationError, ValidationError):
    """An Ed25519 device proof is malformed or invalid."""

    code = "invalid_cli_device_proof"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI device proof is invalid.")


class InvalidCliSessionChallengeError(CliAuthorizationError, ValidationError):
    """A session challenge is malformed, unknown, or unavailable."""

    code = "invalid_cli_session_challenge"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI session challenge is invalid.")


class CliSessionChallengeExpiredError(CliAuthorizationError, ConflictError):
    """A session challenge expired before it was consumed."""

    code = "cli_session_challenge_expired"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI session challenge has expired.")


class InvalidCliSessionError(CliAuthorizationError, AuthenticationError):
    """A CLI bearer session is malformed, expired, or revoked."""

    code = "invalid_cli_session"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI session is invalid.")


class CliDeviceUnavailableError(CliAuthorizationError, NotFoundError):
    """A CLI device is unknown, revoked, or owned by another account."""

    code = "cli_device_unavailable"  # pyrefly: ignore[bad-override]
    default_message = tr(t"The CLI device is unavailable.")


class TooManyActiveCliAuthorizationsError(RateLimitedError, CliAuthorizationError):
    """A client or device has too many outstanding authorization requests."""

    code = "too_many_active_cli_authorizations"  # pyrefly: ignore[bad-override]
    default_message = tr(t"Too many CLI authorization requests are active.")
    _RATE_LIMIT_RETRY_SECONDS: ClassVar[int] = 60

    def __init__(self) -> None:
        super().__init__(self._RATE_LIMIT_RETRY_SECONDS)
