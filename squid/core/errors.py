"""Application errors, shaped for HTTP, Discord, and the CLI alike."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import ClassVar, Self, override

from squid.core.i18n import localization_for, tr
from squid_ui.text import Message, localization_scope

type JSONValue = None | bool | int | float | str | Sequence[JSONValue] | Mapping[str, JSONValue]


def _source_text(value: str | Message) -> str:
    return _source_message(value) if isinstance(value, Message) else value


def _source_message(message: Message) -> str:
    template = message.template
    if message.plural is not None and message.params.get("count") != 1:
        template = message.plural
    params = {
        key: _source_text(value) if isinstance(value, str | Message) else value for key, value in message.params.items()
    }
    return template.format_map(params)


def _translated_text(value: str | Message) -> str:
    return tr(value)


class ErrorCode(StrEnum):
    """Stable machine-readable application error codes."""

    ACCOUNT_ALREADY_LINKED = "ACCOUNT_ALREADY_LINKED"
    ACCOUNT_IDENTITY_NOT_FOUND = "ACCOUNT_IDENTITY_NOT_FOUND"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ALIAS_ALREADY_ADDED = "ALIAS_ALREADY_ADDED"
    ALIAS_ALREADY_CLAIMED = "ALIAS_ALREADY_CLAIMED"
    ALIAS_IN_USE = "ALIAS_IN_USE"
    BUILD_BUSY = "BUILD_BUSY"
    BUILD_NOT_FOUND = "BUILD_NOT_FOUND"
    BUILD_REVISION_MISMATCH = "BUILD_REVISION_MISMATCH"
    BUILD_REVISION_REQUIRED = "BUILD_REVISION_REQUIRED"
    CLAIM_NOT_FOUND = "CLAIM_NOT_FOUND"
    CREATOR_ALIAS_NOT_FOUND = "CREATOR_ALIAS_NOT_FOUND"
    CREATOR_NOT_FOUND = "CREATOR_NOT_FOUND"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    CONSENT_VERSION_STALE = "CONSENT_VERSION_STALE"
    DATA_INTEGRITY_ERROR = "DATA_INTEGRITY_ERROR"
    DOMAIN_ERROR = "DOMAIN_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_BUILD = "INVALID_BUILD"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    INVALID_QUERY = "INVALID_QUERY"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_STATE = "INVALID_STATE"
    INVALID_ACCOUNT = "INVALID_ACCOUNT"
    INVALID_MERGE_CODE = "INVALID_MERGE_CODE"
    INVALID_PROFILE = "INVALID_PROFILE"
    INVALID_VERIFICATION_CODE = "INVALID_VERIFICATION_CODE"
    INVALID_VERSION = "INVALID_VERSION"
    LAST_IDENTITY = "LAST_IDENTITY"
    LINK_RESERVATION_EXPIRED = "LINK_RESERVATION_EXPIRED"
    INVALID_VOTE_CONFIGURATION = "INVALID_VOTE_CONFIGURATION"
    MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
    MINECRAFT_ACCOUNT_NOT_FOUND = "MINECRAFT_ACCOUNT_NOT_FOUND"
    MINECRAFT_SERVICE_UNAVAILABLE = "MINECRAFT_SERVICE_UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    PERSISTENCE_ERROR = "PERSISTENCE_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    RECORD_NOT_FOUND = "RECORD_NOT_FOUND"
    RESTRICTION_NOT_FOUND = "RESTRICTION_NOT_FOUND"
    SCHEMATIC_INVALID = "SCHEMATIC_INVALID"
    SCHEMATIC_NOT_FOUND = "SCHEMATIC_NOT_FOUND"
    SCHEMATIC_RENDER_REFUSED = "SCHEMATIC_RENDER_REFUSED"
    SCHEMATIC_RENDER_UNAVAILABLE = "SCHEMATIC_RENDER_UNAVAILABLE"
    SCHEMATIC_SUPPORT_UNAVAILABLE = "SCHEMATIC_SUPPORT_UNAVAILABLE"
    SCHEMATIC_TIMEOUT = "SCHEMATIC_TIMEOUT"
    SCHEMATIC_TOO_LARGE = "SCHEMATIC_TOO_LARGE"
    SCHEMATIC_WORKER_CRASHED = "SCHEMATIC_WORKER_CRASHED"
    TAG_NOT_FOUND = "TAG_NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VERIFICATION_ATTEMPTS_EXHAUSTED = "VERIFICATION_ATTEMPTS_EXHAUSTED"
    VERSION_CATALOG_UNAVAILABLE = "VERSION_CATALOG_UNAVAILABLE"
    VOTE_SESSION_NOT_FOUND = "VOTE_SESSION_NOT_FOUND"


class SquidError(Exception):
    """Base class for structured application failures."""

    default_message: ClassVar[str | Message] = tr(t"An application error occurred.")
    default_title: ClassVar[str | Message] = tr(t"Application error")
    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    default_resource: ClassVar[str | None] = None
    default_developer_action: ClassVar[str | None] = None
    default_end_user_action: ClassVar[str | Message | None] = None

    def __init__(
        self,
        message: str | Message | None = None,
        *,
        code: ErrorCode | None = None,
        resource: str | None = None,
        context: Mapping[str, JSONValue] | None = None,
        public_context: Mapping[str, JSONValue] | None = None,
        message_params: Mapping[str, JSONValue] | None = None,
        developer_action: str | None = None,
        end_user_action: str | Message | None = None,
        title: str | Message | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.title = title or self.default_title
        self.code = code or self.default_code
        self.resource = resource or self.default_resource
        self.context = dict(context or {})
        self.public_context = dict(public_context or {})
        self.message_params = dict(message_params or {})
        if isinstance(self.message, Message) and self.message_params:
            self.message = replace(self.message, params={**self.message.params, **self.message_params})
        self.developer_action = developer_action or self.default_developer_action
        self.end_user_action = end_user_action or self.default_end_user_action
        super().__init__(self.backend_detail())

    @override
    def __str__(self) -> str:
        return self.backend_detail()

    def _rendered_message(self) -> str:
        if isinstance(self.message, Message):
            return _source_message(self.message)
        return self.message.format(**self.message_params) if self.message_params else self.message

    def backend_detail(self) -> str:
        """Return diagnostic (English) text suitable for logs."""
        message = self._rendered_message()
        if not self.developer_action:
            return message
        # A multi-line message is a rendered list of findings, so the action gets its own
        # line rather than being glued to the end of the last item.
        separator = "\n" if "\n" in message else " "
        return f"{message}{separator}{self.developer_action}"

    def public_detail(self) -> str:
        """Return safe, untranslated (English) text suitable for users and API clients."""
        message = self._rendered_message()
        if self.end_user_action:
            return f"{message} {_source_text(self.end_user_action)}"
        return message

    def localized_title(self, locale: str | None) -> str:
        """Return the error title translated into `locale`."""
        with localization_scope(localization_for(locale)):
            return _translated_text(self.title)

    def localized_public_detail(self, locale: str | None) -> str:
        """Return safe user-facing text translated into `locale`."""
        with localization_scope(localization_for(locale)):
            message = tr(self.message) if isinstance(self.message, Message) else tr(self.message, **self.message_params)
            if self.end_user_action:
                return f"{message} {_translated_text(self.end_user_action)}"
            return message

    def with_context(
        self,
        *,
        context: Mapping[str, JSONValue] | None = None,
        public_context: Mapping[str, JSONValue] | None = None,
        message: str | Message | None = None,
        message_params: Mapping[str, JSONValue] | None = None,
        developer_action: str | None = None,
        end_user_action: str | None = None,
    ) -> Self:
        """Enrich this exception in place while preserving its traceback.

        `message` is here because enrichment that cannot restate the message is only half a helper:
        a layer that resolves *what* the conflict was usually wants to say so, and assigning
        `self.message` by hand skips the `args` refresh at the bottom. Pass the untranslated msgid,
        as a constructor would; `message_params` merges, so a caller can add one placeholder without
        repeating the others.
        """
        if context:
            self.context = {**self.context, **context}
        if public_context:
            self.public_context = {**self.public_context, **public_context}
        if message is not None:
            self.message = message
        if message_params:
            self.message_params = {**self.message_params, **message_params}
            if isinstance(self.message, Message):
                self.message = replace(self.message, params={**self.message.params, **message_params})
        if developer_action is not None:
            self.developer_action = developer_action
        if end_user_action is not None:
            self.end_user_action = end_user_action
        self.args = (self.backend_detail(),)
        return self


class DomainError(SquidError):
    """Base class for expected domain and user-caused failures."""

    default_message = tr(t"The requested operation could not be completed.")
    default_title = tr(t"Request failed")
    default_code = ErrorCode.DOMAIN_ERROR


class ValidationError(DomainError, ValueError):
    """Input does not satisfy an application rule."""

    default_message = tr(t"The supplied value is invalid.")
    default_title = tr(t"Invalid value")
    default_code = ErrorCode.VALIDATION_ERROR


class NotFoundError(DomainError, LookupError):
    """A requested resource does not exist."""

    default_message = tr(t"The requested resource was not found.")
    default_title = tr(t"Resource not found")
    default_code = ErrorCode.NOT_FOUND


class ConflictError(DomainError, RuntimeError):
    """An operation conflicts with current application state."""

    default_message = tr(t"The operation conflicts with the current state.")
    default_title = tr(t"Operation conflict")


class AuthenticationError(DomainError):
    """Authentication credentials are absent or invalid."""

    default_message = tr(t"Unauthorized.")
    default_title = tr(t"Unauthorized")
    default_code = ErrorCode.UNAUTHORIZED


class AuthorizationError(DomainError):
    """The authenticated caller is not allowed to perform an operation."""

    default_message = tr(t"You do not have permission to perform this action.")
    default_title = tr(t"Forbidden")


class RateLimitedError(DomainError):
    """A caller exceeded a bounded operation's abuse-control window."""

    default_message = tr(t"Too many requests. Please try again later.")
    default_title = tr(t"Too many requests")
    default_code = ErrorCode.RATE_LIMITED

    def __init__(self, retry_after: int) -> None:
        super().__init__(public_context={"retry_after": retry_after})
        self.retry_after = retry_after


class InternalError(SquidError):
    """A failure whose diagnostic detail must not be exposed to callers."""

    default_message = tr(t"An internal application error occurred.")
    default_title = tr(t"Internal error")
    default_code = ErrorCode.INTERNAL_ERROR
    default_end_user_action = tr(t"Please try again later.")


class ConfigurationError(InternalError, ValueError):
    """Application configuration is invalid or incomplete."""

    default_message = tr(t"Application configuration is invalid.")
    default_code = ErrorCode.CONFIGURATION_ERROR


class InvalidStateError(InternalError, RuntimeError):
    """Internal objects are in an invalid state for the requested operation."""

    default_message = tr(t"Application state is invalid for this operation.")
    default_code = ErrorCode.INVALID_STATE


class InfrastructureError(InternalError):
    """An infrastructure dependency failed."""

    default_message = tr(t"An infrastructure dependency failed.")
    default_code = ErrorCode.INFRASTRUCTURE_ERROR


class PersistenceError(InfrastructureError):
    """A persistence operation failed."""

    default_message = tr(t"A persistence operation failed.")
    default_code = ErrorCode.PERSISTENCE_ERROR
    default_resource = "database"


class DataIntegrityError(PersistenceError):
    """Persisted data violates application expectations."""

    default_message = tr(t"Persisted data is inconsistent with application expectations.")
    default_code = ErrorCode.DATA_INTEGRITY_ERROR


class ServiceUnavailableError(InfrastructureError):
    """An external service is temporarily unavailable."""

    default_message = tr(t"A required service is temporarily unavailable.")
    default_title = tr(t"Service unavailable")
