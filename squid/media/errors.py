"""Structured media validation and infrastructure errors."""

from enum import StrEnum
from uuid import UUID

from squid.core.errors import (
    ConflictError,
    ErrorCode,
    InfrastructureError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from squid.core.i18n import _
from squid.media.domain.models import MediaViolation


class MediaFailureReason(StrEnum):
    """Stable reason codes that do not expose filenames or media contents."""

    SOURCE_NOT_REGULAR = "source_not_regular"
    SOURCE_CHANGED = "source_changed"
    PROBE_INVALID = "probe_invalid"
    VIDEO_DURATION_UNKNOWN = "video_duration_unknown"
    VIDEO_FRAME_RATE_UNKNOWN = "video_frame_rate_unknown"
    VIDEO_DIMENSIONS_UNSUPPORTED = "video_dimensions_unsupported"
    OUTPUT_EXISTS = "output_exists"
    OUTPUT_INVALID = "output_invalid"
    OUTPUT_MISMATCH = "output_mismatch"
    TOOL_FAILED = "tool_failed"
    TOOL_TIMED_OUT = "tool_timed_out"
    TOOL_UNAVAILABLE = "tool_unavailable"


class MediaLimitExceededError(ValidationError):
    """An upload, output, or decoded-work budget was exceeded."""

    default_message = _("The media exceeds a processing limit.")
    default_code = ErrorCode.INVALID_REQUEST
    default_resource = "media"
    default_end_user_action = _("Choose a smaller or less resource-intensive file and try again.")

    def __init__(self, violation: MediaViolation) -> None:
        super().__init__(
            _("The media exceeds the {measure} limit: {actual} is greater than {limit}."),
            message_params={
                "measure": violation.measure.value,
                "actual": violation.actual,
                "limit": violation.limit,
            },
            context={
                "reason": "limit_exceeded",
                "measure": violation.measure.value,
                "actual": violation.actual,
                "limit": violation.limit,
            },
            public_context={"reason": "limit_exceeded", "measure": violation.measure.value, "limit": violation.limit},
        )
        self.violation = violation


class MediaDraftStateConflictError(ConflictError):
    """A media mutation lost a race with submission finalization."""

    default_message = _("Media cannot be changed while this submission draft is locked.")
    default_title = _("Draft media locked")
    default_resource = "media"
    default_end_user_action = _("Reload the draft before trying again.")

    def __init__(self, status: str) -> None:
        super().__init__(public_context={"reason": "draft_state", "status": status})


class MediaDraftNotFoundError(NotFoundError):
    """A media mutation cannot re-establish ownership after draft deletion."""

    default_message = _("Submission draft not found.")
    default_title = _("Draft not found")
    default_resource = "submission_draft"

    def __init__(self, draft_id: UUID) -> None:
        super().__init__(public_context={"draft_id": str(draft_id)})


class InvalidMediaError(ValidationError):
    """The file or requested transformation is not safe and well-formed."""

    default_message = _("The media file cannot be normalized.")
    default_code = ErrorCode.INVALID_REQUEST
    default_resource = "media"
    default_end_user_action = _("Export the file again in a common image or video format and retry.")

    def __init__(self, reason: MediaFailureReason) -> None:
        super().__init__(
            context={"reason": reason.value},
            public_context={"reason": reason.value},
        )
        self.reason = reason


class MediaToolUnavailableError(ServiceUnavailableError):
    """FFmpeg or ffprobe is absent from the media-worker image."""

    default_message = _("Media processing is temporarily unavailable.")
    default_code = ErrorCode.INFRASTRUCTURE_ERROR
    default_resource = "media"
    default_developer_action = "Install the pinned FFmpeg toolchain in the media-worker image."

    def __init__(self, *, tool: str) -> None:
        super().__init__(context={"reason": MediaFailureReason.TOOL_UNAVAILABLE.value, "tool": tool})
        self.tool = tool


class MediaProcessingError(InfrastructureError):
    """A bounded media subprocess or its output failed validation."""

    default_message = _("Media processing failed.")
    default_resource = "media"
    default_end_user_action = _("Export the file again and retry, or remove it from the submission.")

    def __init__(
        self,
        reason: MediaFailureReason,
        *,
        operation: str,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(
            context={"reason": reason.value, "operation": operation, "exit_code": exit_code},
            public_context={"reason": reason.value, "operation": operation},
        )
        self.reason = reason
        self.operation = operation
        self.exit_code = exit_code


class MediaProcessingTimeoutError(MediaProcessingError, TimeoutError):
    """A media tool exceeded its wall-clock deadline and was terminated."""

    def __init__(self, *, operation: str) -> None:
        super().__init__(MediaFailureReason.TOOL_TIMED_OUT, operation=operation)
