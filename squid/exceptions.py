"""Transport-neutral application exception hierarchy."""

from enum import StrEnum
from typing import ClassVar, Self, override
from uuid import UUID

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]


class ErrorCode(StrEnum):
    """Stable machine-readable application error codes."""

    ACCOUNT_ALREADY_LINKED = "ACCOUNT_ALREADY_LINKED"
    ALIAS_ALREADY_ADDED = "ALIAS_ALREADY_ADDED"
    ALIAS_IN_USE = "ALIAS_IN_USE"
    BUILD_BUSY = "BUILD_BUSY"
    BUILD_NOT_FOUND = "BUILD_NOT_FOUND"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    DATA_INTEGRITY_ERROR = "DATA_INTEGRITY_ERROR"
    DOMAIN_ERROR = "DOMAIN_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_BUILD = "INVALID_BUILD"
    INVALID_MESSAGE = "INVALID_MESSAGE"
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
    RESTRICTION_NOT_FOUND = "RESTRICTION_NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VERSION_CATALOG_UNAVAILABLE = "VERSION_CATALOG_UNAVAILABLE"


class SquidError(Exception):
    """Base class for structured application failures."""

    default_message: ClassVar[str] = "An application error occurred."
    default_title: ClassVar[str] = "Application error"
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
        context: dict[str, JSONValue] | None = None,
        public_context: dict[str, JSONValue] | None = None,
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
        self.developer_action = developer_action or self.default_developer_action
        self.end_user_action = end_user_action or self.default_end_user_action
        super().__init__(self.backend_detail())

    @override
    def __str__(self) -> str:
        return self.backend_detail()

    def backend_detail(self) -> str:
        """Return diagnostic text suitable for logs."""
        if self.developer_action:
            return f"{self.message} {self.developer_action}"
        return self.message

    def public_detail(self) -> str:
        """Return safe text suitable for users and API clients."""
        if self.end_user_action:
            return f"{self.message} {self.end_user_action}"
        return self.message

    def with_context(
        self,
        *,
        context: dict[str, JSONValue] | None = None,
        public_context: dict[str, JSONValue] | None = None,
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

    default_message = "The requested operation could not be completed."
    default_title = "Request failed"
    default_code = ErrorCode.DOMAIN_ERROR


class ValidationError(DomainError, ValueError):
    """Input does not satisfy an application rule."""

    default_message = "The supplied value is invalid."
    default_title = "Invalid value"
    default_code = ErrorCode.VALIDATION_ERROR


class NotFoundError(DomainError, LookupError):
    """A requested resource does not exist."""

    default_message = "The requested resource was not found."
    default_title = "Resource not found"
    default_code = ErrorCode.NOT_FOUND


class ConflictError(DomainError, RuntimeError):
    """An operation conflicts with current application state."""

    default_message = "The operation conflicts with the current state."
    default_title = "Operation conflict"


class AuthenticationError(DomainError):
    """Authentication credentials are absent or invalid."""

    default_message = "Unauthorized."
    default_title = "Unauthorized"
    default_code = ErrorCode.UNAUTHORIZED


class AuthorizationError(DomainError):
    """The authenticated caller is not allowed to perform an operation."""

    default_message = "You do not have permission to perform this action."
    default_title = "Forbidden"


class InternalError(SquidError):
    """A failure whose diagnostic detail must not be exposed to callers."""

    default_message = "An internal application error occurred."
    default_title = "Internal error"
    default_code = ErrorCode.INTERNAL_ERROR
    default_end_user_action = "Please try again later."


class ConfigurationError(InternalError, ValueError):
    """Application configuration is invalid or incomplete."""

    default_message = "Application configuration is invalid."
    default_code = ErrorCode.CONFIGURATION_ERROR


class InvalidStateError(InternalError, RuntimeError):
    """Internal objects are in an invalid state for the requested operation."""

    default_message = "Application state is invalid for this operation."
    default_code = ErrorCode.INVALID_STATE


class InfrastructureError(InternalError):
    """An infrastructure dependency failed."""

    default_message = "An infrastructure dependency failed."
    default_code = ErrorCode.INFRASTRUCTURE_ERROR


class PersistenceError(InfrastructureError):
    """A persistence operation failed."""

    default_message = "A persistence operation failed."
    default_code = ErrorCode.PERSISTENCE_ERROR
    default_resource = "database"


class DataIntegrityError(PersistenceError):
    """Persisted data violates application expectations."""

    default_message = "Persisted data is inconsistent with application expectations."
    default_code = ErrorCode.DATA_INTEGRITY_ERROR


class ServiceUnavailableError(InfrastructureError):
    """An external service is temporarily unavailable."""

    default_message = "A required service is temporarily unavailable."
    default_title = "Service unavailable"


class InvalidBuildError(ValidationError):
    """Build data is invalid."""

    default_message = "The build data is invalid."
    default_code = ErrorCode.INVALID_BUILD
    default_resource = "build"


class BuildNotFoundError(NotFoundError):
    """A build could not be found."""

    default_message = "Build not found."
    default_code = ErrorCode.BUILD_NOT_FOUND
    default_resource = "build"
    default_end_user_action = "Check the build ID and try again."

    def __init__(self, build_id: int, message: str | None = None) -> None:
        super().__init__(
            message,
            context={"build_id": build_id},
            public_context={"build_id": build_id},
        )
        self.build_id = build_id


class BuildBusyError(ConflictError):
    """A build is already being edited."""

    default_message = "This build is currently being edited by someone else."
    default_title = "Build busy"
    default_code = ErrorCode.BUILD_BUSY
    default_resource = "build"
    default_end_user_action = "Wait for the other edit to finish and try again."

    def __init__(self, build_id: int) -> None:
        super().__init__(
            context={"build_id": build_id},
            public_context={"build_id": build_id},
        )
        self.build_id = build_id


class RestrictionNotFoundError(NotFoundError):
    """A restriction name or alias could not be found."""

    default_message = "Restriction not found."
    default_code = ErrorCode.RESTRICTION_NOT_FOUND
    default_resource = "restriction"

    def __init__(self, name: str) -> None:
        super().__init__(
            f"Restriction '{name}' does not exist.",
            context={"name": name},
            public_context={"name": name},
        )
        self.name = name


class AliasAlreadyAddedError(ConflictError):
    """An alias is already attached to the requested restriction."""

    default_message = "That alias is already on this restriction."
    default_title = "Alias already added"
    default_code = ErrorCode.ALIAS_ALREADY_ADDED
    default_resource = "restriction_alias"

    def __init__(self, alias: str, restriction_id: int) -> None:
        super().__init__(
            context={"alias": alias, "restriction_id": restriction_id},
            public_context={"alias": alias},
        )
        self.alias = alias
        self.restriction_id = restriction_id


class AliasInUseError(ConflictError):
    """An alias belongs to a different restriction."""

    default_message = "That alias is already used by another restriction."
    default_title = "Alias in use"
    default_code = ErrorCode.ALIAS_IN_USE
    default_resource = "restriction_alias"

    def __init__(self, alias: str, other_id: int) -> None:
        super().__init__(
            context={"alias": alias, "other_restriction_id": other_id},
            public_context={"alias": alias},
        )
        self.alias = alias
        self.other_id = other_id


class InvalidMessageError(ValidationError):
    """Tracked message metadata is invalid."""

    default_message = "The message metadata is invalid."
    default_code = ErrorCode.INVALID_MESSAGE
    default_resource = "message"


class MessageNotFoundError(NotFoundError):
    """A tracked message could not be found."""

    default_message = "Tracked message not found."
    default_code = ErrorCode.MESSAGE_NOT_FOUND
    default_resource = "message"

    def __init__(self, message_id: int) -> None:
        super().__init__(
            context={"message_id": message_id},
            public_context={"message_id": message_id},
        )
        self.message_id = message_id


class InvalidUserError(ValidationError):
    """User data is invalid."""

    default_message = "The user data is invalid."
    default_code = ErrorCode.INVALID_USER
    default_resource = "user"


class UserNotFoundError(NotFoundError):
    """An application user could not be found."""

    default_message = "User not found."
    default_code = ErrorCode.USER_NOT_FOUND
    default_resource = "user"

    def __init__(self, discord_id: int) -> None:
        super().__init__(context={"discord_id": discord_id})
        self.discord_id = discord_id


class InvalidVerificationCodeError(ValidationError):
    """A verification code is invalid or expired."""

    default_message = "The verification code is invalid or expired."
    default_title = "Invalid verification code"
    default_code = ErrorCode.INVALID_VERIFICATION_CODE
    default_resource = "verification_code"
    default_end_user_action = "Generate a new code and try again."


class AccountAlreadyLinkedError(ConflictError):
    """A Discord account is linked to a different Minecraft account."""

    default_message = "This Discord account is already linked to a different Minecraft account."
    default_title = "Account already linked"
    default_code = ErrorCode.ACCOUNT_ALREADY_LINKED
    default_resource = "user"
    default_end_user_action = "Unlink the current account before linking a new one."

    def __init__(self, discord_id: int, minecraft_uuid: UUID) -> None:
        super().__init__(context={"discord_id": discord_id, "minecraft_uuid": str(minecraft_uuid)})
        self.discord_id = discord_id
        self.minecraft_uuid = minecraft_uuid


class MinecraftAccountNotFoundError(NotFoundError):
    """A Minecraft UUID does not identify an account."""

    default_message = "Minecraft account not found."
    default_code = ErrorCode.MINECRAFT_ACCOUNT_NOT_FOUND
    default_resource = "minecraft_account"
    default_end_user_action = "Check the UUID and try again."

    def __init__(self, minecraft_uuid: UUID) -> None:
        value = str(minecraft_uuid)
        super().__init__(
            context={"minecraft_uuid": value},
            public_context={"minecraft_uuid": value},
        )
        self.minecraft_uuid = minecraft_uuid


class InvalidVersionError(ValidationError):
    """A Minecraft version or version specification is invalid."""

    default_message = "The Minecraft version is invalid."
    default_code = ErrorCode.INVALID_VERSION
    default_resource = "version"
    default_end_user_action = "Use a supported Minecraft version format and try again."


class VersionCatalogUnavailableError(InfrastructureError, RuntimeError):
    """The persisted version catalog is unexpectedly empty."""

    default_message = "The Minecraft version catalog is empty."
    default_code = ErrorCode.VERSION_CATALOG_UNAVAILABLE
    default_resource = "version"
    default_developer_action = "Populate the version catalog before serving version-dependent operations."

    def __init__(self, edition: str) -> None:
        super().__init__(context={"edition": edition})
        self.edition = edition


class InvalidVoteConfigurationError(ConfigurationError):
    """Vote options violate voting policy."""

    default_message = "Vote configuration is invalid."
    default_code = ErrorCode.INVALID_VOTE_CONFIGURATION
    default_resource = "vote"


class MinecraftServiceUnavailableError(ServiceUnavailableError):
    """The Mojang session service failed."""

    default_message = "The Minecraft account service is temporarily unavailable."
    default_code = ErrorCode.MINECRAFT_SERVICE_UNAVAILABLE
    default_resource = "minecraft_account"
    default_end_user_action = "Try again in a few minutes."
