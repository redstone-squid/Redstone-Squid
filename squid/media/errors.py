"""Structured media validation and infrastructure errors."""

from collections.abc import Sequence
from enum import StrEnum
from uuid import UUID

from whenever import Instant

from squid.core.errors import (
    ConflictError,
    DataIntegrityError,
    ErrorCode,
    InfrastructureError,
    InvalidStateError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from squid.core.i18n import tr
from squid.media.domain.models import MediaViolation
from squid_ui.text import Message


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
    """One or more attachment or decoded-work budgets were exceeded."""

    default_message = tr("The attachment exceeds one or more processing limits.")
    default_code = ErrorCode.INVALID_REQUEST
    default_resource = "media"
    default_end_user_action = tr("Choose fewer, smaller, or less resource-intensive attachments and try again.")

    def __init__(self, violations: MediaViolation | Sequence[MediaViolation]) -> None:
        ordered = (violations,) if isinstance(violations, MediaViolation) else tuple(violations)
        if not ordered:
            msg = "MediaLimitExceededError requires at least one violation."
            raise ValueError(msg)
        internal = [
            {"measure": violation.measure.value, "actual": violation.actual, "limit": violation.limit}
            for violation in ordered
        ]
        public = [{"measure": violation.measure.value, "limit": violation.limit} for violation in ordered]
        super().__init__(
            context={
                "reason": "limit_exceeded",
                "violations": internal,
            },
            public_context={"reason": "limit_exceeded", "violations": public},
        )
        self.violations = ordered


class MediaDraftStateConflictError(ConflictError):
    """The draft state does not allow this media mutation."""

    default_message = tr("Media cannot be changed while this submission draft is locked.")
    default_title = tr("Draft media locked")
    default_resource = "media"
    default_end_user_action = tr("Reload the draft before trying again.")

    def __init__(self, status: str) -> None:
        super().__init__(public_context={"reason": "draft_state", "status": status})


class MediaDraftRevisionConflictError(ConflictError):
    """An authorized upload raced with another draft mutation."""

    default_message = tr("The submission draft changed while its attachment was uploading.")
    default_title = tr("Draft changed")
    default_resource = "media"
    default_end_user_action = tr("Reload the draft and upload the attachment again.")

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            context={"expected_revision": expected, "actual_revision": actual},
            public_context={"reason": "draft_revision", "actual_revision": actual},
        )


class MediaDraftNotFoundError(NotFoundError):
    """The draft is missing or does not belong to the expected owner."""

    default_message = tr("Submission draft not found.")
    default_title = tr("Draft not found")
    default_resource = "submission_draft"
    default_end_user_action = tr("Reload your drafts before uploading the attachment again.")

    def __init__(self, draft_id: UUID) -> None:
        super().__init__(public_context={"draft_id": str(draft_id)})


class DraftMediaRequestError(ValidationError):
    """A raw attachment request is ambiguous or violates declared framing."""

    default_message = tr("The draft attachment upload request is invalid.")
    default_title = tr("Invalid attachment upload")
    default_resource = "submission_media"
    default_end_user_action = tr("Check the attachment type and upload it again.")

    def __init__(self, reason: str) -> None:
        super().__init__(public_context={"reason": reason})


class DraftMediaNotFoundError(NotFoundError):
    """No owner-visible attachment upload matches the requested UUID."""

    default_message = tr("Draft attachment not found.")
    default_title = tr("Attachment not found")
    default_resource = "submission_media"
    default_end_user_action = tr("Reload the draft and choose one of its current attachments.")

    def __init__(self, upload_id: UUID) -> None:
        super().__init__(public_context={"upload_id": str(upload_id)})


class DraftMediaConflictError(ConflictError):
    """A caller-provided retry UUID was already used for different upload metadata."""

    default_message = tr("The attachment upload identifier is already in use.")
    default_title = tr("Attachment upload conflict")
    default_resource = "submission_media"
    default_end_user_action = tr("Retry the upload with a new attachment identifier.")

    def __init__(self, upload_id: UUID) -> None:
        super().__init__(public_context={"upload_id": str(upload_id)})


class DraftMediaUnavailableError(ServiceUnavailableError):
    """Draft attachment processing is not enabled for this API process."""

    default_message = tr("Draft attachment processing is temporarily unavailable.")
    default_title = tr("Attachment processing unavailable")
    default_resource = "submission_media"
    default_end_user_action = tr("Keep the attachment locally and try uploading it again later.")


class InvalidMediaError(ValidationError):
    """The source file or requested transformation fails media validation."""

    default_message = tr("The media file cannot be normalized.")
    default_code = ErrorCode.INVALID_REQUEST
    default_resource = "media"
    default_end_user_action = tr("Export the file again in a common image or video format and retry.")

    def __init__(self, reason: MediaFailureReason) -> None:
        super().__init__(
            context={"reason": reason.value},
            public_context={"reason": reason.value},
        )
        self.reason = reason


class MediaToolUnavailableError(ServiceUnavailableError):
    """FFmpeg or ffprobe cannot be found or started."""

    default_message = tr("Media processing is temporarily unavailable.")
    default_code = ErrorCode.INFRASTRUCTURE_ERROR
    default_resource = "media"
    default_developer_action = "Install the pinned FFmpeg toolchain in the media-worker image."

    def __init__(self, *, tool: str) -> None:
        super().__init__(context={"reason": MediaFailureReason.TOOL_UNAVAILABLE.value, "tool": tool})
        self.tool = tool


class MediaProcessingError(InfrastructureError):
    """A bounded media subprocess or its output failed validation."""

    default_message = tr("Media processing failed.")
    default_resource = "media"
    default_end_user_action = tr("Export the file again and retry, or remove it from the submission.")

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


def _upload_conflict_message(upload_id: object) -> Message:
    upload_id = str(upload_id)
    return tr(t"Media upload {upload_id} already exists with different metadata.")


class MediaUploadConflictError(ConflictError):
    """An upload UUID was retried with different immutable metadata."""

    default_resource = "media_upload"

    def __init__(
        self,
        upload_id: UUID,
        *,
        existing_source_object_key: str,
        existing_status: object,
    ) -> None:
        super().__init__(_upload_conflict_message(upload_id))
        self.upload_id = upload_id
        self.existing_source_object_key = existing_source_object_key
        self.existing_status = existing_status


class MediaJobSourceError(DataIntegrityError):
    """A queued raw object is absent, oversized, or no longer matches its metadata."""

    default_message = tr("The queued raw media object is inconsistent with its metadata.")
    default_resource = "media_upload"


class MediaJobArtifactError(DataIntegrityError):
    """A media artifact is unreadable, disagrees with its report, or was not confirmed by storage."""

    default_message = tr("Object storage did not confirm a normalized media artifact.")
    default_resource = "media_artifact"


class MediaArtifactCleanupInProgressError(ConflictError):
    """Publication must wait while an object is claimed for cleanup."""

    default_message = tr("A normalized media object is being cleaned up.")
    default_resource = "media_artifact"

    def __init__(self, retry_at: Instant) -> None:
        super().__init__()
        self.retry_at = retry_at


class MediaJobClaimLostError(InvalidStateError):
    """A worker must stop after its durable claim token is revoked or reclaimed."""

    default_message = tr("The media job claim is no longer valid.")
    default_resource = "media_job"
