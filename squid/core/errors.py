"""Shared transport-neutral application errors."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import ClassVar, Self, override

from squid.core.i18n import _, translate

type JSONValue = None | bool | int | float | str | Sequence[JSONValue] | Mapping[str, JSONValue]


class ErrorCode(StrEnum):
    """Stable machine-readable application error codes."""

    ACCOUNT_ALREADY_LINKED = "ACCOUNT_ALREADY_LINKED"
    ALIAS_ALREADY_ADDED = "ALIAS_ALREADY_ADDED"
    ALIAS_ALREADY_CLAIMED = "ALIAS_ALREADY_CLAIMED"
    ALIAS_IN_USE = "ALIAS_IN_USE"
    BUILD_BUSY = "BUILD_BUSY"
    BUILD_NOT_FOUND = "BUILD_NOT_FOUND"
    BUILD_REVISION_MISMATCH = "BUILD_REVISION_MISMATCH"
    BUILD_REVISION_REQUIRED = "BUILD_REVISION_REQUIRED"
    CLAIM_NOT_FOUND = "CLAIM_NOT_FOUND"
    CREATOR_ALIAS_NOT_FOUND = "CREATOR_ALIAS_NOT_FOUND"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    DATA_INTEGRITY_ERROR = "DATA_INTEGRITY_ERROR"
    DOMAIN_ERROR = "DOMAIN_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_BUILD = "INVALID_BUILD"
    INVALID_CURSOR = "INVALID_CURSOR"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    INVALID_QUERY = "INVALID_QUERY"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_STATE = "INVALID_STATE"
    INVALID_USER = "INVALID_USER"
    INVALID_VERIFICATION_CODE = "INVALID_VERIFICATION_CODE"
    INVALID_VERSION = "INVALID_VERSION"
    INVALID_VOTE_CONFIGURATION = "INVALID_VOTE_CONFIGURATION"
    MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
    MINECRAFT_ACCOUNT_NOT_FOUND = "MINECRAFT_ACCOUNT_NOT_FOUND"
    MINECRAFT_SERVICE_UNAVAILABLE = "MINECRAFT_SERVICE_UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    RESTRICTION_NOT_FOUND = "RESTRICTION_NOT_FOUND"
    SCHEMATIC_INVALID = "SCHEMATIC_INVALID"
    SCHEMATIC_NOT_FOUND = "SCHEMATIC_NOT_FOUND"
    SCHEMATIC_RENDER_UNAVAILABLE = "SCHEMATIC_RENDER_UNAVAILABLE"
    SCHEMATIC_SUPPORT_UNAVAILABLE = "SCHEMATIC_SUPPORT_UNAVAILABLE"
    SCHEMATIC_TIMEOUT = "SCHEMATIC_TIMEOUT"
    SCHEMATIC_TOO_LARGE = "SCHEMATIC_TOO_LARGE"
    SCHEMATIC_WORKER_CRASHED = "SCHEMATIC_WORKER_CRASHED"
    UNAUTHORIZED = "UNAUTHORIZED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VERSION_CATALOG_UNAVAILABLE = "VERSION_CATALOG_UNAVAILABLE"


class SquidError(Exception):
    """Base class for structured application failures."""

    default_message: ClassVar[str] = _("An application error occurred.")
    default_title: ClassVar[str] = _("Application error")
    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    default_resource: ClassVar[str | None] = None
    default_developer_action: ClassVar[str | None] = None
    default_end_user_action: ClassVar[str | None] = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | None = None,
        resource: str | None = None,
        context: Mapping[str, JSONValue] | None = None,
        public_context: Mapping[str, JSONValue] | None = None,
        message_params: Mapping[str, JSONValue] | None = None,
        developer_action: str | None = None,
        end_user_action: str | None = None,
        title: str | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.title = title or self.default_title
        self.code = code or self.default_code
        self.resource = resource or self.default_resource
        self.context = dict(context or {})
        self.public_context = dict(public_context or {})
        self.message_params = dict(message_params or {})
        self.developer_action = developer_action or self.default_developer_action
        self.end_user_action = end_user_action or self.default_end_user_action
        super().__init__(self.backend_detail())

    @override
    def __str__(self) -> str:
        return self.backend_detail()

    def _rendered_message(self) -> str:
        return self.message.format(**self.message_params) if self.message_params else self.message

    def backend_detail(self) -> str:
        """Return diagnostic (English) text suitable for logs."""
        message = self._rendered_message()
        if self.developer_action:
            return f"{message} {self.developer_action}"
        return message

    def public_detail(self) -> str:
        """Return safe, untranslated (English) text suitable for users and API clients."""
        message = self._rendered_message()
        if self.end_user_action:
            return f"{message} {self.end_user_action}"
        return message

    def localized_title(self, locale: str | None) -> str:
        """Return the error title translated into `locale`."""
        return translate(locale, self.title)

    def localized_public_detail(self, locale: str | None) -> str:
        """Return safe user-facing text translated into `locale`."""
        message = translate(locale, self.message, **self.message_params)
        if self.end_user_action:
            return f"{message} {translate(locale, self.end_user_action)}"
        return message

    def with_context(
        self,
        *,
        context: Mapping[str, JSONValue] | None = None,
        public_context: Mapping[str, JSONValue] | None = None,
        developer_action: str | None = None,
        end_user_action: str | None = None,
    ) -> Self:
        """Enrich this exception in place while preserving its traceback."""
        if context:
            self.context = {**self.context, **context}
        if public_context:
            self.public_context = {**self.public_context, **public_context}
        if developer_action is not None:
            self.developer_action = developer_action
        if end_user_action is not None:
            self.end_user_action = end_user_action
        self.args = (self.backend_detail(),)
        return self


class DomainError(SquidError):
    """Base class for expected domain and user-caused failures."""

    default_message = _("The requested operation could not be completed.")
    default_title = _("Request failed")
    default_code = ErrorCode.DOMAIN_ERROR


class ValidationError(DomainError, ValueError):
    """Input does not satisfy an application rule."""

    default_message = _("The supplied value is invalid.")
    default_title = _("Invalid value")
    default_code = ErrorCode.VALIDATION_ERROR


class NotFoundError(DomainError, LookupError):
    """A requested resource does not exist."""

    default_message = _("The requested resource was not found.")
    default_title = _("Resource not found")
    default_code = ErrorCode.NOT_FOUND


class ConflictError(DomainError, RuntimeError):
    """An operation conflicts with current application state."""

    default_message = _("The operation conflicts with the current state.")
    default_title = _("Operation conflict")


class AuthenticationError(DomainError):
    """Authentication credentials are absent or invalid."""

    default_message = _("Unauthorized.")
    default_title = _("Unauthorized")
    default_code = ErrorCode.UNAUTHORIZED


class AuthorizationError(DomainError):
    """The authenticated caller is not allowed to perform an operation."""

    default_message = _("You do not have permission to perform this action.")
    default_title = _("Forbidden")


class RateLimitedError(DomainError):
    """A caller exceeded a bounded operation's abuse-control window."""

    default_message = _("Too many requests. Please try again later.")
    default_title = _("Too many requests")
    default_code = ErrorCode.RATE_LIMITED

    def __init__(self, retry_after: int) -> None:
        super().__init__(public_context={"retry_after": retry_after})
        self.retry_after = retry_after


class InternalError(SquidError):
    """A failure whose diagnostic detail must not be exposed to callers."""

    default_message = _("An internal application error occurred.")
    default_title = _("Internal error")
    default_code = ErrorCode.INTERNAL_ERROR
    default_end_user_action = _("Please try again later.")


class ConfigurationError(InternalError, ValueError):
    """Application configuration is invalid or incomplete."""

    default_message = _("Application configuration is invalid.")
    default_code = ErrorCode.CONFIGURATION_ERROR


class InvalidStateError(InternalError, RuntimeError):
    """Internal objects are in an invalid state for the requested operation."""

    default_message = _("Application state is invalid for this operation.")
    default_code = ErrorCode.INVALID_STATE


class InfrastructureError(InternalError):
    """An infrastructure dependency failed."""

    default_message = _("An infrastructure dependency failed.")
    default_code = ErrorCode.INFRASTRUCTURE_ERROR


class PersistenceError(InfrastructureError):
    """A persistence operation failed."""

    default_message = _("A persistence operation failed.")
    default_code = ErrorCode.PERSISTENCE_ERROR
    default_resource = "database"


class DataIntegrityError(PersistenceError):
    """Persisted data violates application expectations."""

    default_message = _("Persisted data is inconsistent with application expectations.")
    default_code = ErrorCode.DATA_INTEGRITY_ERROR


class ServiceUnavailableError(InfrastructureError):
    """An external service is temporarily unavailable."""

    default_message = _("A required service is temporarily unavailable.")
    default_title = _("Service unavailable")
