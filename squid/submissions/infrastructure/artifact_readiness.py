"""Backend-authoritative artifact readiness for synchronized drafts."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from squid.media.application.jobs import (
    MediaArtifactRole,
    MediaJobSnapshot,
    MediaJobStatus,
    StoredMediaArtifact,
)
from squid.media.domain import MediaBatchTotals, MediaKind, MediaLimits
from squid.submissions.domain.finalization import (
    SchematicArtifactState,
    SubmissionArtifactReadiness,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)

_ACTIVE_MEDIA_STATES = {MediaJobStatus.PENDING, MediaJobStatus.CLAIMED, MediaJobStatus.COMPLETED}


class DraftMediaJobReader(Protocol):
    """Read server-owned normalization jobs associated with one draft."""

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]: ...


@dataclass(frozen=True, slots=True)
class DraftSchematicSnapshot:
    """Backend state and stored artifact identity for one draft schematic."""

    state: SchematicArtifactState
    artifact_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.state is SchematicArtifactState.READY) != (self.artifact_id is not None):
            msg = "Only a ready schematic state may carry a stored artifact ID."
            raise ValueError(msg)
        if self.artifact_id is not None and self.artifact_id.int == 0:
            msg = "Schematic artifact identifiers cannot be nil UUIDs."
            raise ValueError(msg)


class DraftSchematicReader(Protocol):
    """Read stored upload identity and validation status from backend-owned records.

    A ready artifact must belong to the requested draft and have passed the file
    format and resource limits. Client answers are never proof of an uploaded file.
    """

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot: ...


class EmptyDraftSchematicReader:
    """Report no schematic while the draft upload endpoint is not implemented."""

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot:
        return DraftSchematicSnapshot(SchematicArtifactState.ABSENT)


class AuthoritativeDraftArtifactReadiness:
    """Assess only worker-owned media jobs and backend-owned schematic uploads."""

    def __init__(
        self,
        media: DraftMediaJobReader,
        schematics: DraftSchematicReader,
        *,
        media_limits: MediaLimits | None = None,
    ) -> None:
        self._media = media
        self._schematics = schematics
        self._media_limits = media_limits or MediaLimits()

    async def assess(self, draft_id: UUID) -> SubmissionArtifactReadiness:
        """Return opaque verified IDs and stable repair states for one draft."""
        jobs = await self._media.list_for_draft(draft_id)
        schematic = await self._schematics.read_for_draft(draft_id)
        media_ids, issues = _assess_media(draft_id, jobs, self._media_limits)
        return SubmissionArtifactReadiness(
            schematic_state=schematic.state,
            schematic_id=schematic.artifact_id,
            normalized_media_upload_ids=media_ids,
            issues=issues,
        )


def _assess_media(
    draft_id: UUID,
    jobs: Sequence[MediaJobSnapshot],
    limits: MediaLimits,
) -> tuple[tuple[UUID, ...], tuple[SubmissionAttentionIssue, ...]]:
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
            if artifact.role in {MediaArtifactRole.OUTPUT, MediaArtifactRole.POSTER}
        ),
    )
    rejected |= limits.batch_violation(totals) is not None

    identifiers: list[UUID] = []
    seen: set[UUID] = set()
    for job in retained:
        if job.upload.draft_id != draft_id:
            rejected = True
            continue
        if job.status is not MediaJobStatus.COMPLETED:
            continue
        if job.upload.id in seen or not _valid_completed_job(job, limits):
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


def _valid_completed_job(job: MediaJobSnapshot, limits: MediaLimits) -> bool:
    if (
        job.completed_at is None
        or job.claimed_at is not None
        or job.claim_token is not None
        or job.dead_at is not None
        or job.discarded_at is not None
    ):
        return False
    expected = {MediaArtifactRole.OUTPUT, MediaArtifactRole.REPORT}
    if job.upload.kind is MediaKind.VIDEO:
        expected.add(MediaArtifactRole.POSTER)
    roles = [artifact.role for artifact in job.artifacts]
    if len(roles) != len(set(roles)) or set(roles) != expected:
        return False
    artifacts = {artifact.role: artifact for artifact in job.artifacts}
    output = artifacts[MediaArtifactRole.OUTPUT]
    report = artifacts[MediaArtifactRole.REPORT]
    expected_output_type = "image/png" if job.upload.kind is MediaKind.IMAGE else "video/mp4"
    if output.content_type != expected_output_type or not _valid_visual_artifact(output, limits):
        return False
    if report.content_type != "application/json" or report.width is not None or report.height is not None:
        return False
    if job.upload.kind is MediaKind.VIDEO:
        poster = artifacts[MediaArtifactRole.POSTER]
        if poster.content_type != "image/jpeg" or not _valid_visual_artifact(poster, limits):
            return False
    return True


def _valid_visual_artifact(artifact: StoredMediaArtifact, limits: MediaLimits) -> bool:
    return (
        artifact.width is not None
        and artifact.height is not None
        and artifact.width * artifact.height <= limits.max_pixels_per_frame
    )
