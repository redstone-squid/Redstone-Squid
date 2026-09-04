"""Authoritative submission artifact readiness."""

from dataclasses import replace
from uuid import UUID

import pytest
from whenever import Instant

from squid.media.application.jobs import (
    MediaArtifactRole,
    MediaJobSnapshot,
    MediaJobStatus,
    MediaUploadMetadata,
    StoredMediaArtifact,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.submissions.domain import (
    SchematicArtifactState,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
)
from squid.submissions.infrastructure.artifact_readiness import (
    AuthoritativeDraftArtifactReadiness,
    DraftSchematicSnapshot,
    FailClosedDraftSchematicReader,
    SanitizerIssuedSchematic,
    SchematicSanitizationPolicyFacts,
    SchematicSanitizationReportFacts,
)

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000601")
OTHER_DRAFT_ID = UUID("00000000-0000-4000-8000-000000000602")
SCHEMATIC_ID = UUID("00000000-0000-4000-8000-000000000603")
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")


class FakeMediaJobs:
    def __init__(self, jobs: tuple[MediaJobSnapshot, ...] = ()) -> None:
        self.jobs = jobs
        self.requested: list[UUID] = []

    async def list_for_draft(self, draft_id: UUID) -> tuple[MediaJobSnapshot, ...]:
        self.requested.append(draft_id)
        return self.jobs


class FakeSchematics:
    def __init__(self, snapshot: DraftSchematicSnapshot | None = None) -> None:
        self.snapshot = snapshot or DraftSchematicSnapshot(SchematicArtifactState.ABSENT)
        self.requested: list[UUID] = []

    async def read_for_draft(self, draft_id: UUID) -> DraftSchematicSnapshot:
        self.requested.append(draft_id)
        return self.snapshot


class FakePresence:
    def __init__(self, *, supplied: bool) -> None:
        self.supplied = supplied
        self.requested: list[UUID] = []

    async def has_supplied_schematic(self, draft_id: UUID) -> bool:
        self.requested.append(draft_id)
        return self.supplied


def _artifact(
    role: MediaArtifactRole,
    *,
    content_type: str,
    byte_size: int = 100,
    width: int | None = 16,
    height: int | None = 9,
) -> StoredMediaArtifact:
    return StoredMediaArtifact(
        role=role,
        object_key=f"private/{role.value}",
        content_type=content_type,
        byte_size=byte_size,
        sha256="a" * 64,
        width=width,
        height=height,
    )


def _completed_artifacts(kind: MediaKind) -> tuple[StoredMediaArtifact, ...]:
    artifacts = [
        _artifact(
            MediaArtifactRole.OUTPUT,
            content_type="image/png" if kind is MediaKind.IMAGE else "video/mp4",
        )
    ]
    if kind is MediaKind.VIDEO:
        artifacts.append(_artifact(MediaArtifactRole.VIDEO_THUMBNAIL, content_type="image/jpeg"))
    artifacts.append(
        _artifact(
            MediaArtifactRole.REPORT,
            content_type="application/json",
            width=None,
            height=None,
        )
    )
    return tuple(artifacts)


def _job(
    number: int,
    *,
    kind: MediaKind = MediaKind.IMAGE,
    status: MediaJobStatus = MediaJobStatus.COMPLETED,
    draft_id: UUID = DRAFT_ID,
    source_bytes: int = 100,
    artifacts: tuple[StoredMediaArtifact, ...] | None = None,
) -> MediaJobSnapshot:
    upload_id = UUID(f"00000000-0000-4000-8000-{number:012d}")
    completed = status is MediaJobStatus.COMPLETED
    claimed = status is MediaJobStatus.CLAIMED
    return MediaJobSnapshot(
        upload=MediaUploadMetadata(
            id=upload_id,
            draft_id=draft_id,
            kind=kind,
            source_content_type="image/png" if kind is MediaKind.IMAGE else "video/mp4",
            source_byte_size=source_bytes,
            source_sha256="b" * 64,
            source_object_key=f"private/raw/{number}",
            strip_audio=False,
        ),
        status=status,
        attempts=1,
        available_at=NOW,
        claimed_at=NOW if claimed else None,
        claim_token=UUID("00000000-0000-4000-8000-000000000699") if claimed else None,
        completed_at=NOW if completed else None,
        dead_at=NOW if status is MediaJobStatus.DEAD else None,
        discarded_at=NOW if status is MediaJobStatus.DISCARDED else None,
        last_error="rejected" if status is MediaJobStatus.DEAD else None,
        artifacts=_completed_artifacts(kind) if artifacts is None and completed else (artifacts or ()),
    )


async def test_empty_backend_state_is_absent_and_ready() -> None:
    media = FakeMediaJobs()
    schematics = FakeSchematics()

    readiness = await AuthoritativeDraftArtifactReadiness(media, schematics).assess(DRAFT_ID)

    assert readiness.schematic_state is SchematicArtifactState.ABSENT
    assert readiness.normalized_media_upload_ids == ()
    assert readiness.issues == ()
    assert media.requested == [DRAFT_ID]
    assert schematics.requested == [DRAFT_ID]


async def test_discarded_media_is_ignored() -> None:
    discarded = _job(1, status=MediaJobStatus.DISCARDED, source_bytes=10_000)

    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((discarded,)),
        FakeSchematics(),
        media_limits=MediaLimits(max_source_bytes=1),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == ()
    assert readiness.issues == ()


async def test_pending_and_claimed_media_return_one_stable_processing_issue() -> None:
    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((_job(1, status=MediaJobStatus.PENDING), _job(2, status=MediaJobStatus.CLAIMED))),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.issues == (SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_PROCESSING),)


async def test_dead_media_returns_a_stable_rejected_issue_without_diagnostics() -> None:
    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((_job(1, status=MediaJobStatus.DEAD),)),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == ()
    assert readiness.issues == (SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_REJECTED),)


@pytest.mark.parametrize("kind", [MediaKind.IMAGE, MediaKind.VIDEO])
async def test_valid_completed_media_exposes_only_opaque_upload_ids(kind: MediaKind) -> None:
    job = _job(1, kind=kind)

    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((job,)),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == (job.upload.id,)
    assert readiness.issues == ()
    assert not hasattr(readiness, "object_key")
    assert not hasattr(readiness, "sha256")


@pytest.mark.parametrize(
    "artifacts",
    [
        (_artifact(MediaArtifactRole.OUTPUT, content_type="image/png"),),
        (
            _artifact(MediaArtifactRole.OUTPUT, content_type="image/jpeg"),
            _artifact(MediaArtifactRole.REPORT, content_type="application/json", width=None, height=None),
        ),
        (
            _artifact(MediaArtifactRole.OUTPUT, content_type="image/png"),
            _artifact(MediaArtifactRole.REPORT, content_type="text/plain", width=None, height=None),
        ),
    ],
)
async def test_invalid_completed_image_artifacts_are_rejected(
    artifacts: tuple[StoredMediaArtifact, ...],
) -> None:
    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((_job(1, artifacts=artifacts),)),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == ()
    assert readiness.issues == (SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_REJECTED),)


async def test_completed_video_requires_a_valid_thumbnail() -> None:
    artifacts = (
        _artifact(MediaArtifactRole.OUTPUT, content_type="video/mp4"),
        _artifact(MediaArtifactRole.REPORT, content_type="application/json", width=None, height=None),
    )

    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((_job(1, kind=MediaKind.VIDEO, artifacts=artifacts),)),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == ()
    assert readiness.issues == (SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_REJECTED),)


async def test_completed_video_accepts_a_legacy_poster_during_rolling_upgrade() -> None:
    artifacts = (
        _artifact(MediaArtifactRole.OUTPUT, content_type="video/mp4"),
        _artifact(MediaArtifactRole.POSTER, content_type="image/jpeg"),
        _artifact(MediaArtifactRole.REPORT, content_type="application/json", width=None, height=None),
    )

    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((_job(1, kind=MediaKind.VIDEO, artifacts=artifacts),)),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == (UUID("00000000-0000-4000-8000-000000000001"),)
    assert readiness.issues == ()


@pytest.mark.parametrize(
    "limits",
    [
        MediaLimits(max_images=1),
        MediaLimits(max_source_bytes=199),
        MediaLimits(max_output_bytes=199),
    ],
)
async def test_media_limits_are_rechecked_over_the_whole_active_draft(limits: MediaLimits) -> None:
    first = _job(1)
    second = _job(2)

    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((first, second)),
        FakeSchematics(),
        media_limits=limits,
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == (first.upload.id, second.upload.id)
    assert readiness.issues == (SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_REJECTED),)


async def test_wrong_draft_media_is_rejected_and_never_exposed() -> None:
    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs((_job(1, draft_id=OTHER_DRAFT_ID),)),
        FakeSchematics(),
    ).assess(DRAFT_ID)

    assert readiness.normalized_media_upload_ids == ()
    assert readiness.issues == (SubmissionAttentionIssue("media", SubmissionAttentionReason.MEDIA_REJECTED),)


async def test_sanitized_state_requires_and_projects_a_sanitizer_certificate() -> None:
    issued = SanitizerIssuedSchematic(
        artifact_id=SCHEMATIC_ID,
        sanitizer_version="nucleation-0.11.0",
        policy=SchematicSanitizationPolicyFacts(
            policy_key="submission_v1",
            include_inventories=False,
            include_free_text=False,
        ),
        report=SchematicSanitizationReportFacts(
            schema_version=1,
            action_counts=(("inventory_items_removed", 3), ("text_fields_removed", 2)),
        ),
    )

    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs(),
        FakeSchematics(DraftSchematicSnapshot(SchematicArtifactState.SANITIZED, issued)),
    ).assess(DRAFT_ID)

    assert readiness.schematic_state is SchematicArtifactState.SANITIZED
    assert readiness.sanitized_schematic_id == SCHEMATIC_ID


@pytest.mark.parametrize(
    "snapshot",
    [
        DraftSchematicSnapshot(SchematicArtifactState.PROCESSING),
        DraftSchematicSnapshot(SchematicArtifactState.REJECTED),
    ],
)
async def test_nonterminal_schematic_state_never_exposes_an_identifier(snapshot: DraftSchematicSnapshot) -> None:
    readiness = await AuthoritativeDraftArtifactReadiness(
        FakeMediaJobs(),
        FakeSchematics(snapshot),
    ).assess(DRAFT_ID)

    assert readiness.schematic_state is snapshot.state
    assert readiness.sanitized_schematic_id is None


async def test_fail_closed_reader_reports_absent_without_backend_presence() -> None:
    presence = FakePresence(supplied=False)

    snapshot = await FailClosedDraftSchematicReader(presence).read_for_draft(DRAFT_ID)

    assert snapshot == DraftSchematicSnapshot(SchematicArtifactState.ABSENT)
    assert presence.requested == [DRAFT_ID]


async def test_fail_closed_reader_rejects_supplied_bytes_without_a_sanitizer() -> None:
    snapshot = await FailClosedDraftSchematicReader(FakePresence(supplied=True)).read_for_draft(DRAFT_ID)

    assert snapshot == DraftSchematicSnapshot(SchematicArtifactState.REJECTED)
    assert snapshot.sanitized is None


def test_schematic_snapshot_cannot_claim_sanitized_without_a_certificate() -> None:
    with pytest.raises(ValueError, match="sanitized schematic state"):
        DraftSchematicSnapshot(SchematicArtifactState.SANITIZED)


def test_sanitizer_report_rejects_sensitive_free_form_values_by_construction() -> None:
    report = SchematicSanitizationReportFacts(schema_version=1, action_counts=(("text_removed", 1),))
    assert report.action_counts == (("text_removed", 1),)

    with pytest.raises(ValueError, match="stable identifiers"):
        replace(report, action_counts=(("removed: secret sign text", 1),))
