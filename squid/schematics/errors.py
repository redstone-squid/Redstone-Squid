"""Schematic context errors."""

from squid.core.errors import (
    ErrorCode,
    InfrastructureError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from squid.core.i18n import _


class InvalidSchematicError(ValidationError):
    """A file is not a schematic this application can read."""

    default_message = _("The file is not a readable Minecraft schematic.")
    default_code = ErrorCode.SCHEMATIC_INVALID
    default_resource = "schematic"
    default_end_user_action = _("Upload a .litematic, .schem, .schematic, .nbt, or .mcstructure file.")


class SchematicTooLargeError(ValidationError):
    """A schematic exceeds a configured size budget."""

    default_message = _("The schematic is too large to process.")
    default_code = ErrorCode.SCHEMATIC_TOO_LARGE
    default_resource = "schematic"
    default_end_user_action = _("Crop the schematic to the build itself and try again.")

    def __init__(self, *, actual: int, limit: int, measure: str) -> None:
        super().__init__(
            _("The schematic is too large: {measure} is {actual}, limit is {limit}."),
            message_params={"measure": measure, "actual": actual, "limit": limit},
            context={"measure": measure, "actual": actual, "limit": limit},
            public_context={"measure": measure, "limit": limit},
        )
        self.actual = actual
        self.limit = limit
        self.measure = measure


class DecompressionBudgetExceededError(SchematicTooLargeError):
    """A compressed upload inflates past the allowed budget.

    Raised before any byte reaches the native engine, so a decompression bomb costs us only
    the streaming read that detected it.
    """

    def __init__(self, *, limit: int) -> None:
        super().__init__(actual=limit + 1, limit=limit, measure="inflated size")


class SchematicNotFoundError(NotFoundError):
    """A stored schematic could not be found."""

    default_message = _("Schematic not found.")
    default_code = ErrorCode.SCHEMATIC_NOT_FOUND
    default_resource = "schematic"


class SchematicSupportUnavailableError(ServiceUnavailableError):
    """The native schematic engine is not installed on this instance."""

    default_message = _("Schematic support is not available on this instance.")
    default_code = ErrorCode.SCHEMATIC_SUPPORT_UNAVAILABLE
    default_resource = "schematic"
    default_developer_action = "Install the optional 'schematics' extra to enable this feature."


class SchematicRenderUnavailableError(ServiceUnavailableError):
    """Rendering is disabled, unconfigured, or has no usable GPU adapter."""

    default_message = _("Schematic rendering is not configured on this instance.")
    default_code = ErrorCode.SCHEMATIC_RENDER_UNAVAILABLE
    default_resource = "schematic"
    default_developer_action = "Configure a resource pack and verify a Vulkan adapter is available."


class SchematicTimeoutError(InfrastructureError, TimeoutError):
    """A schematic operation exceeded its deadline and its worker was killed."""

    default_message = _("The schematic operation took too long and was cancelled.")
    default_code = ErrorCode.SCHEMATIC_TIMEOUT
    default_resource = "schematic"

    def __init__(self, *, operation: str, timeout_seconds: float) -> None:
        super().__init__(
            context={"operation": operation, "timeout_seconds": timeout_seconds},
            public_context={"operation": operation},
        )
        self.operation = operation
        self.timeout_seconds = timeout_seconds


class SchematicWorkerCrashedError(InfrastructureError, RuntimeError):
    """A schematic worker process died while handling a request.

    The supervisor respawns the worker; the request itself is never retried, because
    retrying a payload that just killed a process is how you build a crash loop.
    """

    default_message = _("The schematic engine failed while reading this file.")
    default_code = ErrorCode.SCHEMATIC_WORKER_CRASHED
    default_resource = "schematic"

    def __init__(self, *, operation: str, exit_code: int | None = None) -> None:
        super().__init__(
            context={"operation": operation, "exit_code": exit_code},
            public_context={"operation": operation},
        )
        self.operation = operation
        self.exit_code = exit_code
