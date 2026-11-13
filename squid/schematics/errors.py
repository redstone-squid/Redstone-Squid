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
from squid_i18n import Text, md_tr


class InvalidSchematicError(ValidationError):
    """A file is not a schematic this application can read."""

    default_message = tr(t"schematics-file-not-readable = The file is not a readable Minecraft schematic.")
    default_code = ErrorCode.SCHEMATIC_INVALID
    default_resource = "schematic"
    default_end_user_action = tr(
        t"schematics-upload-litematic-schem = Upload a .litematic, .schem, .schematic, .nbt, or .mcstructure file."
    )


class AmbiguousSimulationInputError(InvalidSchematicError):
    """No usable input was selected, or several controls could be actuated.

    candidates lists valid lever and button coordinates for the caller to choose. rejected records
    an explicit coordinate that was not usable; only the candidates are included in public context.
    """

    def __init__(
        self,
        *,
        candidates: Iterable[Vector3] = (),
        rejected: Vector3 | None = None,
    ) -> None:
        ordered = tuple(sorted(candidates))
        if rejected is not None:
            message = tr(
                t"schematics-input-coordinate-gave = The input coordinate you gave "
                t"is not a lever or button in this schematic."
            )
            action = tr(t"schematics-pick-coordinates-listed = Pick one of the coordinates listed below.")
        elif ordered:
            message = tr(
                t"schematics-schematic-several-possible = This schematic has "
                t"several possible inputs, so choosing one for you would be unsafe."
            )
            action = tr(t"schematics-run-command = Run the command again with one of the coordinates listed below.")
        else:
            message = tr(
                t"schematics-schematic-no-input = This schematic has no "
                t"input annotation and no lever or button to actuate."
            )
            action = md_tr(
                t"schematics-errors-add-an-io-input-sign-or-use = Add an @io.* input sign, or use a "
                t"schematic with an interactable input.",
            )
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

    default_message = tr(t"schematics-too-large-process = The schematic is too large to process.")
    default_code = ErrorCode.SCHEMATIC_TOO_LARGE
    default_resource = "schematic"
    default_end_user_action = tr(
        t"schematics-crop-schematic-build = Crop the schematic to the build itself and try again."
    )

    def __init__(self, *, actual: int, limit: int, measure: str) -> None:
        super().__init__(
            md_tr(
                t"schematics-too-large-limit = The schematic is too large: {measure} is {actual}, limit is {limit}.",
            ),
            context={"measure": measure, "actual": actual, "limit": limit},
            public_context={"measure": measure, "limit": limit},
        )
        self.actual = actual
        self.limit = limit
        self.measure = measure


class DecompressionBudgetExceededError(SchematicTooLargeError):
    """The inflated upload exceeds its budget before native parsing.

    actual is reported as limit + 1 because the streaming check stops once it knows the limit was
    exceeded, without measuring the entire decompressed file.
    """

    def __init__(self, *, limit: int) -> None:
        super().__init__(actual=limit + 1, limit=limit, measure="inflated size")


class SchematicNotFoundError(NotFoundError):
    """A stored schematic could not be found."""

    default_message = tr(t"schematics-errors-schematic-not-found = Schematic not found.")
    default_code = ErrorCode.SCHEMATIC_NOT_FOUND
    default_resource = "schematic"


class SchematicSupportUnavailableError(ServiceUnavailableError):
    """The native schematic engine is not installed on this instance."""

    default_message = tr(t"schematics-support-unavailable = Schematic support is not available on this instance.")
    default_code = ErrorCode.SCHEMATIC_SUPPORT_UNAVAILABLE
    default_resource = "schematic"
    default_developer_action = "Install the optional 'schematics' extra to enable this feature."


class SchematicRenderRefusedError(ConflictError):
    """The attachment is ineligible for a preview under the current configuration.

    The public reason lets callers explain a skip such as a quarantined file or an exceeded budget.
    Changing the source or configuration may make a later attempt eligible.
    """

    default_message = tr(t"schematics-build-schematic-previewed = This build's schematic cannot be previewed.")
    default_code = ErrorCode.SCHEMATIC_RENDER_REFUSED
    default_resource = "schematic"

    def __init__(self, reason: str, description: str | Text) -> None:
        super().__init__(description, context={"reason": reason}, public_context={"reason": reason})
        self.reason = reason


class SchematicRenderUnavailableError(ServiceUnavailableError):
    """Rendering is disabled, unconfigured, or has no usable GPU adapter."""

    default_message = tr(t"schematics-schematic-unavailable = Schematic rendering is unavailable.")
    default_code = ErrorCode.SCHEMATIC_RENDER_UNAVAILABLE
    default_resource = "schematic"
    default_developer_action = "Check the schematic render configuration and worker logs for the cause."


class SchematicTimeoutError(InfrastructureError, TimeoutError):
    """A schematic operation or wait for a durable result exceeded its deadline.

    The subprocess adapter kills a worker running a timed-out request. A durable client timeout
    only stops polling; the queued job can continue.
    """

    default_message = tr(
        t"schematics-schematic-operation-took = The schematic operation took too long and was cancelled."
    )
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

    The pool can start a replacement for later work, but does not replay the failed request.
    Services can remember its digest to refuse further attempts during their lifetime.
    """

    default_message = tr(t"schematics-schematic-engine-failed = The schematic engine failed while reading this file.")
    default_code = ErrorCode.SCHEMATIC_WORKER_CRASHED
    default_resource = "schematic"

    def __init__(self, *, operation: str, exit_code: int | None = None) -> None:
        super().__init__(
            context={"operation": operation, "exit_code": exit_code},
            public_context={"operation": operation},
        )
        self.operation = operation
        self.exit_code = exit_code
