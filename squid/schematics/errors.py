"""Schematic context errors."""

from collections.abc import Iterable

from squid.core.errors import (
    ConflictError,
    ErrorCode,
    InfrastructureError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from squid.core.i18n import tr
from squid.schematics.domain.models import Vector3
from squid_ui.text import Message


class InvalidSchematicError(ValidationError):
    """A file is not a schematic this application can read."""

    default_message = tr(t"The file is not a readable Minecraft schematic.")
    default_code = ErrorCode.SCHEMATIC_INVALID
    default_resource = "schematic"
    default_end_user_action = tr(t"Upload a .litematic, .schem, .schematic, .nbt, or .mcstructure file.")


class AmbiguousSimulationInputError(InvalidSchematicError):
    """The tick simulator could not be told which control to actuate.

    Choosing between several levers would silently time a different circuit than the one a
    moderator meant, so the engine refuses instead. Refusing is only useful if the caller is
    told what to choose *between*, which is why the candidates travel as public context: they
    are coordinates inside a file the caller uploaded, so there is nothing to withhold.
    """

    def __init__(
        self,
        *,
        candidates: Iterable[Vector3] = (),
        rejected: Vector3 | None = None,
    ) -> None:
        ordered = tuple(sorted(candidates))
        if rejected is not None:
            message = tr(t"The input coordinate you gave is not a lever or button in this schematic.")
            action = tr(t"Pick one of the coordinates listed below.")
        elif ordered:
            message = tr(t"This schematic has several possible inputs, so choosing one for you would be unsafe.")
            action = tr(t"Run the command again with one of the coordinates listed below.")
        else:
            message = tr(t"This schematic has no input annotation and no lever or button to actuate.")
            action = tr(t"Add an @io.* input sign, or use a schematic with an interactable input.")
        encoded = [list(candidate) for candidate in ordered]
        super().__init__(
            message,
            end_user_action=action,
            context={"input_candidates": encoded, "rejected": list(rejected) if rejected is not None else None},
            public_context={"input_candidates": encoded},
        )
        self.candidates = ordered
        self.rejected = rejected


class SchematicTooLargeError(ValidationError):
    """A schematic exceeds a configured size budget."""

    default_message = tr(t"The schematic is too large to process.")
    default_code = ErrorCode.SCHEMATIC_TOO_LARGE
    default_resource = "schematic"
    default_end_user_action = tr(t"Crop the schematic to the build itself and try again.")

    def __init__(self, *, actual: int, limit: int, measure: str) -> None:
        super().__init__(
            tr(t"The schematic is too large: {measure} is {actual}, limit is {limit}."),
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

    default_message = tr(t"Schematic not found.")
    default_code = ErrorCode.SCHEMATIC_NOT_FOUND
    default_resource = "schematic"


class SchematicSupportUnavailableError(ServiceUnavailableError):
    """The native schematic engine is not installed on this instance."""

    default_message = tr(t"Schematic support is not available on this instance.")
    default_code = ErrorCode.SCHEMATIC_SUPPORT_UNAVAILABLE
    default_resource = "schematic"
    default_developer_action = "Install the optional 'schematics' extra to enable this feature."


class SchematicRenderRefusedError(ConflictError):
    """This attachment will never render under the requested recipe.

    A refusal is about the stored schematic, not about the request: an unsanitized file, one
    that already crashed the engine, or one past a preview budget answers the same way however
    the camera is pointed. The reason travels as public context because it is the only thing
    that tells a caller whether to fix something or to stop asking.
    """

    default_message = tr(t"This build's schematic cannot be previewed.")
    default_code = ErrorCode.SCHEMATIC_RENDER_REFUSED
    default_resource = "schematic"

    def __init__(self, reason: str, description: str | Message) -> None:
        super().__init__(description, context={"reason": reason}, public_context={"reason": reason})
        self.reason = reason


class SchematicRenderUnavailableError(ServiceUnavailableError):
    """Rendering is disabled, unconfigured, or has no usable GPU adapter."""

    default_message = tr(t"Schematic rendering is unavailable.")
    default_code = ErrorCode.SCHEMATIC_RENDER_UNAVAILABLE
    default_resource = "schematic"
    default_developer_action = "Check the schematic render configuration and worker logs for the cause."


class SchematicTimeoutError(InfrastructureError, TimeoutError):
    """A schematic operation exceeded its deadline and its worker was killed."""

    default_message = tr(t"The schematic operation took too long and was cancelled.")
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

    default_message = tr(t"The schematic engine failed while reading this file.")
    default_code = ErrorCode.SCHEMATIC_WORKER_CRASHED
    default_resource = "schematic"

    def __init__(self, *, operation: str, exit_code: int | None = None) -> None:
        super().__init__(
            context={"operation": operation, "exit_code": exit_code},
            public_context={"operation": operation},
        )
        self.operation = operation
        self.exit_code = exit_code
