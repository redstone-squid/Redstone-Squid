"""Backend-authoritative artifact readiness for synchronized drafts."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from squid.core.actors import ActingContext
from squid.core.errors import InvalidStateError
from squid.core.media.application.jobs import MediaJobSnapshot
from squid.core.media.domain import MediaBatchTotals, MediaKind, MediaLimits
from squid.core.media.domain.jobs import MediaArtifactRole, MediaJobStatus
from squid.core.media.domain.models import IMAGE_CONTENT_TYPES, VIDEO_CONTENT_TYPES
from squid.core.submissions.domain.finalization import (
    SchematicArtifactState,
    SubmissionArtifactReadiness,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)

_ACTIVE_MEDIA_STATES = {MediaJobStatus.PENDING, MediaJobStatus.CLAIMED, MediaJobStatus.COMPLETED}


class DraftMediaJobReader(Protocol):
    """Read server-owned normalization jobs associated with one draft."""

    async def list_for_draft(self, draft_id: UUID, *, context: ActingContext) -> Sequence[MediaJobSnapshot]: ...


@dataclass(frozen=True, slots=True)
class DraftSchematicSnapshot:
    """Backend state and stored artifact identity for one draft schematic."""

    state: SchematicArtifactState
    artifact_id: UUID | None = None
    issues: tuple[SubmissionAttentionIssue, ...] = ()

    def __post_init__(self) -> None:
        if (self.state is SchematicArtifactState.READY) != (self.artifact_id is not None):
            msg = "Only a ready schematic state may carry a stored artifact ID."
            raise InvalidStateError(msg)
        if self.artifact_id is not None and self.artifact_id.int == 0:
            msg = "Schematic artifact identifiers cannot be nil UUIDs."
            raise InvalidStateError(msg)


class DraftSchematicReader(Protocol):
    """Read stored upload identity and validation status from backend-owned records.

    A ready artifact must belong to the requested draft and have passed the file
    format and resource limits. Client answers are never proof of an uploaded file.
    """

    async def read_for_draft(self, draft_id: UUID, *, context: ActingContext) -> DraftSchematicSnapshot: ...


class EmptyDraftSchematicReader:
    """Report no schematic for hosts that do not provide a schematic reader."""

    async def read_for_draft(self, draft_id: UUID, *, context: ActingContext) -> DraftSchematicSnapshot:
        return DraftSchematicSnapshot(SchematicArtifactState.ABSENT)


class DraftIntakeReader(Protocol):
    """Read unresolved supplied-file requirements before accepting artifact absence."""

    async def issues_for_draft(
        self, draft_id: UUID, *, context: ActingContext
    ) -> tuple[SubmissionAttentionIssue, ...]: ...


class AuthoritativeDraftArtifactReadiness:
    """Check whether the draft's attachments are ready for build creation.

    Media jobs must have completed with the expected output files and within
    media limits. The schematic reader supplies the selected schematic's state.
    Intake issues also block submission, keeping failed downloads visible even
    when no media job or schematic upload was created for them.
    """

    def __init__(
        self,
        media: DraftMediaJobReader,
        schematics: DraftSchematicReader,
        *,
        media_limits: MediaLimits | None = None,
        intake: DraftIntakeReader | None = None,
    ) -> None:
        self._intake = intake
        self._media = media
        self._schematics = schematics
        self._media_limits = media_limits or MediaLimits()

    async def assess(self, draft_id: UUID, *, context: ActingContext) -> SubmissionArtifactReadiness:
        """Return verified artifact IDs and any issues that prevent finalization."""
        jobs = await self._media.list_for_draft(draft_id, context=context)
        schematic = await self._schematics.read_for_draft(draft_id, context=context)
        media_ids, issues = _assess_media(draft_id, jobs, self._media_limits)
        intake_issues = (
            await self._intake.issues_for_draft(draft_id, context=context) if self._intake is not None else ()
        )
        return SubmissionArtifactReadiness(
            schematic_state=schematic.state,
            schematic_id=schematic.artifact_id,
            normalized_media_upload_ids=media_ids,
            issues=(*issues, *schematic.issues, *intake_issues),
        )


def _assess_media(
    draft_id: UUID,
    jobs: Sequence[MediaJobSnapshot],
    limits: MediaLimits,
) -> tuple[tuple[UUID, ...], tuple[SubmissionAttentionIssue, ...]]:
    # Discarded uploads no longer block submission or count toward batch limits.
    retained = [job for job in jobs if job.status is not MediaJobStatus.DISCARDED]
    processing = any(job.status in {MediaJobStatus.PENDING, MediaJobStatus.CLAIMED} for job in retained)
    rejected = any(job.status is MediaJobStatus.DEAD for job in retained)

    active = [job for job in retained if job.status in _ACTIVE_MEDIA_STATES]
    totals = MediaBatchTotals(
        image_count=sum(job.upload.kind is MediaKind.IMAGE for job in active),
        video_count=sum(job.upload.kind is MediaKind.VIDEO for job in active),
        source_bytes=sum(job.upload.source_byte_size for job in active),
        output_bytes=sum(
            artifact.byte_size
            for job in active
            if job.status is MediaJobStatus.COMPLETED
            for artifact in job.artifacts
            if artifact.role in {MediaArtifactRole.OUTPUT, MediaArtifactRole.VIDEO_THUMBNAIL}
        ),
    )
    rejected |= bool(limits.batch_violations(totals))

    identifiers: list[UUID] = []
    seen: set[UUID] = set()
    for job in retained:
        if job.upload.draft_id != draft_id:
            rejected = True
            continue
        if job.status is not MediaJobStatus.COMPLETED:
            continue
        if job.upload.id in seen or not _valid_completed_job(job):
            rejected = True
            continue
        seen.add(job.upload.id)
        identifiers.append(job.upload.id)

    issues: list[SubmissionAttentionIssue] = []
    if processing:
        issues.append(SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_PROCESSING))
    if rejected:
        issues.append(SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_REJECTED))
    return tuple(identifiers), tuple(issues)


def _valid_completed_job(job: MediaJobSnapshot) -> bool:
    """Check completion metadata, required artifact roles, content types, and dimensions."""
    if (
        job.completed_at is None
        or job.claimed_at is not None
        or job.claim_token is not None
        or job.dead_at is not None
        or job.discarded_at is not None
    ):
        return False
    expected = {MediaArtifactRole.OUTPUT, MediaArtifactRole.REPORT}
    roles = [artifact.role for artifact in job.artifacts]
    if job.upload.kind is MediaKind.VIDEO:
        expected.add(MediaArtifactRole.VIDEO_THUMBNAIL)
    if len(roles) != len(set(roles)) or set(roles) != expected:
        return False
    artifacts = {artifact.role: artifact for artifact in job.artifacts}
    output = artifacts[MediaArtifactRole.OUTPUT]
    report = artifacts[MediaArtifactRole.REPORT]
    expected_output_types = IMAGE_CONTENT_TYPES if job.upload.kind is MediaKind.IMAGE else VIDEO_CONTENT_TYPES
    if (
        output.content_type not in expected_output_types
        or output.width is None
        or output.height is None
        or output.width <= 0
        or output.height <= 0
    ):
        return False
    if report.content_type != "application/json" or report.width is not None or report.height is not None:
        return False
    if job.upload.kind is MediaKind.VIDEO:
        thumbnail = artifacts[MediaArtifactRole.VIDEO_THUMBNAIL]
        if (
            thumbnail.content_type != "image/jpeg"
            or thumbnail.width is None
            or thumbnail.height is None
            or thumbnail.width <= 0
            or thumbnail.height <= 0
        ):
            return False
    return True
