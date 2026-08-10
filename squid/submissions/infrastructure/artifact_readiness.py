"""Backend-authoritative artifact readiness for synchronized drafts."""

import re
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

_STABLE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ACTIVE_MEDIA_STATES = {MediaJobStatus.PENDING, MediaJobStatus.CLAIMED, MediaJobStatus.COMPLETED}


class DraftMediaJobReader(Protocol):
    """Read server-owned normalization jobs associated with one draft."""

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]: ...


@dataclass(frozen=True, slots=True)
class SchematicSanitizationPolicyFacts:
    """Exact backend policy applied by a format-aware schematic sanitizer."""

    policy_key: str
    include_inventories: bool
    include_free_text: bool

    def __post_init__(self) -> None:
        if _STABLE_KEY.fullmatch(self.policy_key) is None:
            msg = "Schematic sanitizer policy keys must be stable identifiers."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SchematicSanitizationReportFacts:
    """Non-sensitive counts emitted by the sanitizer, never removed values."""

    schema_version: int
    action_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.action_counts]
        if self.schema_version < 1 or len(keys) != len(set(keys)):
            msg = "Schematic sanitizer reports require a positive schema version and unique action keys."
            raise ValueError(msg)
        if any(_STABLE_KEY.fullmatch(key) is None or count < 0 for key, count in self.action_counts):
            msg = "Schematic sanitizer report actions must be stable identifiers with nonnegative counts."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SanitizerIssuedSchematic:
    """Opaque canonical artifact identity and its server-issued sanitization certificate."""

    artifact_id: UUID
    sanitizer_version: str
    policy: SchematicSanitizationPolicyFacts
    report: SchematicSanitizationReportFacts

    def __post_init__(self) -> None:
        if self.artifact_id.int == 0:
            msg = "Sanitized schematic artifact identifiers cannot be nil UUIDs."
            raise ValueError(msg)
        if (
            not self.sanitizer_version
            or self.sanitizer_version != self.sanitizer_version.strip()
            or len(self.sanitizer_version) > 120
        ):
            msg = "Schematic sanitizer versions must be 1-120 characters without surrounding whitespace."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DraftSchematicSnapshot:
    """Backend state for one draft schematic and, only when complete, its certificate."""

    state: SchematicArtifactState
    sanitized: SanitizerIssuedSchematic | None = None

    def __post_init__(self) -> None:
        if (self.state is SchematicArtifactState.SANITIZED) != (self.sanitized is not None):
            msg = "Only a sanitized schematic state may carry a sanitizer-issued artifact."
            raise ValueError(msg)


class DraftSchematicReader(Protocol):
    """Future sanitizer repository port consumed by finalization.

    ``read_for_draft`` must read backend-owned quarantine and worker records. It may
    return ``SANITIZED`` only after a claim-fenced sanitizer transaction persisted a
    canonical artifact UUID, sanitizer version, exact applied policy, and non-sensitive
    report counts together. The applied inventory/text flags must match the current
    persisted draft policy; those client choices are requirements, never evidence that
    sanitization occurred. Implementations must never infer success from draft answers,
    filenames, extensions, object keys, hashes, serialization, or format conversion.

    The Nucleation-backed implementation should implement this exact port once
    Schem-at/Nucleation#10 ships a released format-aware sanitizer.
    """

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot: ...


class DraftSchematicPresenceReader(Protocol):
    """Read whether backend quarantine has ever accepted a schematic for a draft."""

    async def has_supplied_schematic(self, draft_id: UUID) -> bool: ...


class FailClosedDraftSchematicReader:
    """Reject quarantined schematics while Nucleation#10 is unavailable.

    With no quarantine reader, the only truthful result is ``ABSENT``. If backend
    quarantine reports that bytes were supplied, they are ``REJECTED`` because this
    implementation has no sanitizer and deliberately has no ``SANITIZED`` path.
    """

    def __init__(self, presence: DraftSchematicPresenceReader | None = None) -> None:
        self._presence = presence

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot:
        if self._presence is not None and await self._presence.has_supplied_schematic(draft_id):
            return DraftSchematicSnapshot(SchematicArtifactState.REJECTED)
        return DraftSchematicSnapshot(SchematicArtifactState.ABSENT)


class AuthoritativeDraftArtifactReadiness:
    """Assess only worker-owned media jobs and sanitizer-owned schematic state."""

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
            sanitized_schematic_id=(schematic.sanitized.artifact_id if schematic.sanitized is not None else None),
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
