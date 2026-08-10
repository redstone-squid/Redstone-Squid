"""Durable media job service and runner behavior."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import override
from uuid import UUID, uuid4

import pytest
from whenever import Instant

from squid.artifacts import ArtifactMetadata
from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.jobs import (
    ClaimedMediaJob,
    MediaArtifactRole,
    MediaEnqueueOutcome,
    MediaJobFailureOutcome,
    MediaJobSnapshot,
    MediaJobStatus,
    MediaNormalizationJobRunner,
    MediaNormalizationJobService,
    MediaUploadConflictError,
    MediaUploadMetadata,
    MediaUploadSubmission,
    StagedMediaUploadSubmission,
    StoredMediaArtifact,
    TerminalMediaSource,
)
from squid.media.application.models import MediaNormalizationResult
from squid.media.application.services import MediaNormalizationService
from squid.media.domain import (
    MediaArtifact,
    MediaBatchTotals,
    MediaKind,
    MediaLimits,
    MediaNormalizationAction,
    MediaNormalizationReport,
    MediaProbe,
)
from squid.media.errors import (
    InvalidMediaError,
    MediaDraftNotFoundError,
    MediaDraftStateConflictError,
    MediaFailureReason,
    MediaLimitExceededError,
)

NOW = Instant.parse_iso("2026-08-11T12:00:00Z")
DRAFT_ID = UUID("84ab2da9-c27e-4d37-98c6-973bcc92f5e4")
UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")


class MemoryArtifacts:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        self.objects[key] = bytes(data)
        self.content_types[key] = content_type
        return ArtifactMetadata(len(data), hashlib.sha256(data).hexdigest())

    async def put_path(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        max_bytes: int,
    ) -> ArtifactMetadata:
        data = source.read_bytes()
        if len(data) > max_bytes:
            raise ValueError
        return await self.put(key, data, content_type=content_type)

    async def get(self, key: str, *, max_bytes: int) -> bytes | None:
        data = self.objects.get(key)
        if data is not None and len(data) > max_bytes:
            raise ValueError
        return data

    async def get_path(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactMetadata | None:
        data = await self.get(key, max_bytes=max_bytes)
        if data is None:
            return None
        destination.write_bytes(data)
        return ArtifactMetadata(len(data), hashlib.sha256(data).hexdigest())

    async def stat(self, key: str) -> ArtifactMetadata | None:
        data = self.objects.get(key)
        return None if data is None else ArtifactMetadata(len(data), hashlib.sha256(data).hexdigest())

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.content_types.pop(key, None)

    async def aclose(self) -> None:
        pass


@dataclass(slots=True)
class _JobState:
    upload: MediaUploadMetadata
    status: MediaJobStatus = MediaJobStatus.PENDING
    attempts: int = 0
    claim_token: UUID | None = None
    claimed_at: Instant | None = None
    completed_at: Instant | None = None
    dead_at: Instant | None = None
    discarded_at: Instant | None = None
    last_error: str | None = None
    artifacts: tuple[StoredMediaArtifact, ...] = ()


class MemoryMediaJobs:
    def __init__(self) -> None:
        self.states: dict[UUID, _JobState] = {}

    async def enqueue(self, upload: MediaUploadMetadata, limits: MediaLimits) -> MediaEnqueueOutcome:
        existing = self.states.get(upload.id)
        if existing is not None:
            if replace(existing.upload, created_at=None, raw_deleted_at=None) != upload:
                raise MediaUploadConflictError(
                    upload.id,
                    existing_source_object_key=existing.upload.source_object_key,
                    existing_status=existing.status,
                )
            return MediaEnqueueOutcome(created=False, status=existing.status)
        totals = MediaBatchTotals(
            image_count=sum(
                state.upload.kind is MediaKind.IMAGE and state.status is not MediaJobStatus.DISCARDED
                for state in self.states.values()
            )
            + int(upload.kind is MediaKind.IMAGE),
            video_count=sum(
                state.upload.kind is MediaKind.VIDEO and state.status is not MediaJobStatus.DISCARDED
                for state in self.states.values()
            )
            + int(upload.kind is MediaKind.VIDEO),
            source_bytes=sum(
                state.upload.source_byte_size
                for state in self.states.values()
                if state.status not in {MediaJobStatus.DEAD, MediaJobStatus.DISCARDED}
            )
            + upload.source_byte_size,
        )
        violation = limits.batch_violation(totals)
        if violation is not None:
            raise MediaLimitExceededError(violation)
        self.states[upload.id] = _JobState(replace(upload, created_at=NOW))
        return MediaEnqueueOutcome(created=True, status=MediaJobStatus.PENDING)

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        state = self.states.get(upload_id)
        if state is None:
            return None
        return MediaJobSnapshot(
            upload=state.upload,
            status=state.status,
            attempts=state.attempts,
            available_at=NOW,
            claimed_at=state.claimed_at,
            claim_token=state.claim_token,
            completed_at=state.completed_at,
            dead_at=state.dead_at,
            discarded_at=state.discarded_at,
            last_error=state.last_error,
            artifacts=state.artifacts,
        )

    async def list_for_draft(self, draft_id: UUID) -> tuple[MediaJobSnapshot, ...]:
        snapshots = [await self.get(upload_id) for upload_id in self.states]
        return tuple(
            snapshot for snapshot in snapshots if snapshot is not None and snapshot.upload.draft_id == draft_id
        )

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        state = self.states.get(upload_id)
        if state is None or state.upload.draft_id != draft_id:
            return False
        state.status = MediaJobStatus.DISCARDED
        state.claim_token = None
        state.claimed_at = None
        state.completed_at = None
        state.dead_at = None
        state.discarded_at = NOW
        return True

    async def claim(self, *, limit: int) -> tuple[ClaimedMediaJob, ...]:
        claimed: list[ClaimedMediaJob] = []
        for state in self.states.values():
            if state.status is not MediaJobStatus.PENDING or len(claimed) >= limit:
                continue
            token = uuid4()
            state.status = MediaJobStatus.CLAIMED
            state.claim_token = token
            state.claimed_at = NOW
            claimed.append(ClaimedMediaJob(state.upload, state.attempts, NOW, token))
        return tuple(claimed)

    async def complete(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
        limits: MediaLimits,
    ) -> bool:
        del limits
        state = self.states[job.upload.id]
        if state.status is not MediaJobStatus.CLAIMED or state.claim_token != job.claim_token:
            return False
        state.status = MediaJobStatus.COMPLETED
        state.claim_token = None
        state.claimed_at = None
        state.completed_at = NOW
        state.artifacts = tuple(artifacts)
        return True

    async def fail(
        self,
        job: ClaimedMediaJob,
        error: str,
        *,
        max_attempts: int,
        terminal: bool,
    ) -> MediaJobFailureOutcome:
        state = self.states[job.upload.id]
        if state.status is not MediaJobStatus.CLAIMED or state.claim_token != job.claim_token:
            return MediaJobFailureOutcome(applied=False, dead=False)
        state.attempts += 1
        dead = terminal or state.attempts >= max_attempts
        state.status = MediaJobStatus.DEAD if dead else MediaJobStatus.PENDING
        state.claim_token = None
        state.claimed_at = None
        state.dead_at = NOW if dead else None
        state.discarded_at = None
        state.last_error = error
        return MediaJobFailureOutcome(applied=True, dead=dead)

    async def terminal_sources(self, *, limit: int) -> tuple[TerminalMediaSource, ...]:
        return tuple(
            TerminalMediaSource(state.upload.id, state.upload.source_object_key)
            for state in self.states.values()
            if state.status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD, MediaJobStatus.DISCARDED}
            and state.upload.raw_deleted_at is None
        )[:limit]

    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool:
        state = self.states[source.upload_id]
        if state.upload.raw_deleted_at is not None:
            return False
        state.upload = replace(state.upload, raw_deleted_at=NOW)
        return True


class RejectingMediaJobs(MemoryMediaJobs):
    def __init__(self, error: MediaDraftNotFoundError | MediaDraftStateConflictError) -> None:
        super().__init__()
        self.error = error

    @override
    async def enqueue(self, upload: MediaUploadMetadata, limits: MediaLimits) -> MediaEnqueueOutcome:
        del upload, limits
        raise self.error


class WritingNormalizer:
    def __init__(self, kind: MediaKind, *, failure: Exception | None = None) -> None:
        self.kind = kind
        self.failure = failure
        self.seen_directories: list[Path] = []

    async def probe(self, source_path: Path) -> MediaProbe:
        self.seen_directories.append(source_path.parent)
        if self.failure is not None:
            raise self.failure
        if self.kind is MediaKind.IMAGE:
            return MediaProbe(("jpeg",), "mjpeg", 2, 2, 0, 1, None)
        return MediaProbe(("mp4",), "h264", 2, 2, 30, 1, 1_000, audio_codec="aac", frame_count=30)

    async def normalize(
        self,
        request: MediaNormalizationRequest,
        *,
        probe: MediaProbe,
        source_bytes: int,
        limits: MediaLimits,
    ) -> MediaNormalizationResult:
        del limits
        output = b"normalized-video" if self.kind is MediaKind.VIDEO else b"normalized-image"
        request.output_path.write_bytes(output)
        output_probe = MediaProbe(
            ("mp4",) if self.kind is MediaKind.VIDEO else ("png",),
            "h264" if self.kind is MediaKind.VIDEO else "png",
            2,
            2,
            30 if self.kind is MediaKind.VIDEO else 0,
            1,
            1_000 if self.kind is MediaKind.VIDEO else None,
            audio_codec="aac" if self.kind is MediaKind.VIDEO else None,
            frame_count=30 if self.kind is MediaKind.VIDEO else None,
        )
        output_artifact = MediaArtifact(
            "video/mp4" if self.kind is MediaKind.VIDEO else "image/png",
            len(output),
            hashlib.sha256(output).hexdigest(),
            2,
            2,
        )
        poster_artifact = None
        if request.poster_path is not None:
            poster = b"poster"
            request.poster_path.write_bytes(poster)
            poster_artifact = MediaArtifact(
                "image/jpeg",
                len(poster),
                hashlib.sha256(poster).hexdigest(),
                2,
                2,
            )
        report = MediaNormalizationReport(
            kind=self.kind,
            source_bytes=source_bytes,
            input_probe=probe,
            output_probe=output_probe,
            output=output_artifact,
            poster=poster_artifact,
            actions=(
                MediaNormalizationAction.VIDEO_TRANSCODED
                if self.kind is MediaKind.VIDEO
                else MediaNormalizationAction.IMAGE_REENCODED,
            ),
        )
        return MediaNormalizationResult(request.output_path, request.poster_path, report)

    async def discard(self, result: MediaNormalizationResult) -> None:
        result.output_path.unlink(missing_ok=True)
        if result.poster_path is not None:
            result.poster_path.unlink(missing_ok=True)

    async def aclose(self) -> None:
        pass


async def test_submit_is_retry_safe_and_does_not_overwrite_conflicting_source() -> None:
    artifacts = MemoryArtifacts()
    repository = MemoryMediaJobs()
    jobs = MediaNormalizationJobService(repository, artifacts)
    submission = MediaUploadSubmission(
        draft_id=DRAFT_ID,
        kind=MediaKind.IMAGE,
        source=b"image",
        source_content_type="image/jpeg",
        upload_id=UPLOAD_ID,
    )

    assert await jobs.submit(submission) == UPLOAD_ID
    assert await jobs.submit(submission) == UPLOAD_ID
    original = await jobs.get(UPLOAD_ID)
    assert original is not None
    original_key = original.upload.source_object_key

    try:
        await jobs.submit(replace(submission, source=b"different"))
    except MediaUploadConflictError:
        pass
    else:
        raise AssertionError

    assert artifacts.objects[original_key] == b"image"
    assert all(data != b"different" for data in artifacts.objects.values())


async def test_submit_staged_streams_a_regular_file_and_rejects_empty_source(tmp_path: Path) -> None:
    artifacts = MemoryArtifacts()
    repository = MemoryMediaJobs()
    jobs = MediaNormalizationJobService(repository, artifacts)
    source = tmp_path / "source"
    source.write_bytes(b"image")

    upload_id = await jobs.submit_staged(
        StagedMediaUploadSubmission(
            draft_id=DRAFT_ID,
            kind=MediaKind.IMAGE,
            source_path=source,
            source_content_type="image/jpeg",
            upload_id=UPLOAD_ID,
        )
    )

    snapshot = await jobs.get(upload_id)
    assert snapshot is not None
    assert snapshot.upload.source_byte_size == 5
    assert artifacts.objects[snapshot.upload.source_object_key] == b"image"

    source.write_bytes(b"")
    try:
        await jobs.submit_staged(
            StagedMediaUploadSubmission(
                draft_id=DRAFT_ID,
                kind=MediaKind.IMAGE,
                source_path=source,
                source_content_type="image/jpeg",
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError


@pytest.mark.parametrize(
    "error",
    [MediaDraftStateConflictError("processing"), MediaDraftNotFoundError(DRAFT_ID)],
)
async def test_submit_removes_staged_source_when_draft_rejects_registration(
    error: MediaDraftNotFoundError | MediaDraftStateConflictError,
) -> None:
    artifacts = MemoryArtifacts()
    jobs = MediaNormalizationJobService(RejectingMediaJobs(error), artifacts)

    with pytest.raises(type(error)):
        await jobs.submit(
            MediaUploadSubmission(
                draft_id=DRAFT_ID,
                kind=MediaKind.IMAGE,
                source=b"image",
                source_content_type="image/jpeg",
                upload_id=UPLOAD_ID,
            )
        )

    assert artifacts.objects == {}


async def test_draft_capacity_is_reserved_atomically_and_discard_releases_it() -> None:
    artifacts = MemoryArtifacts()
    repository = MemoryMediaJobs()
    jobs = MediaNormalizationJobService(repository, artifacts, limits=MediaLimits(max_images=1))
    first_id = await jobs.submit(
        MediaUploadSubmission(
            draft_id=DRAFT_ID,
            kind=MediaKind.IMAGE,
            source=b"first",
            source_content_type="image/png",
        )
    )

    with pytest.raises(MediaLimitExceededError) as exc_info:
        await jobs.submit(
            MediaUploadSubmission(
                draft_id=DRAFT_ID,
                kind=MediaKind.IMAGE,
                source=b"second",
                source_content_type="image/png",
            )
        )
    assert exc_info.value.violation.measure.value == "image_count"
    assert all(data != b"second" for data in artifacts.objects.values())

    assert await jobs.discard(DRAFT_ID, first_id)
    second_id = await jobs.submit(
        MediaUploadSubmission(
            draft_id=DRAFT_ID,
            kind=MediaKind.IMAGE,
            source=b"second",
            source_content_type="image/png",
        )
    )
    snapshots = await jobs.list_for_draft(DRAFT_ID)
    assert {snapshot.upload.id for snapshot in snapshots} == {first_id, second_id}
    assert next(snapshot for snapshot in snapshots if snapshot.upload.id == first_id).status is MediaJobStatus.DISCARDED


async def test_runner_persists_video_output_poster_and_report_then_cleans_raw_and_temp(tmp_path: Path) -> None:
    artifacts = MemoryArtifacts()
    repository = MemoryMediaJobs()
    jobs = MediaNormalizationJobService(repository, artifacts)
    submission = MediaUploadSubmission(
        draft_id=DRAFT_ID,
        kind=MediaKind.VIDEO,
        source=b"raw-video",
        source_content_type="video/quicktime",
        upload_id=UPLOAD_ID,
    )
    await jobs.submit(submission)
    normalizer = WritingNormalizer(MediaKind.VIDEO)
    runner = MediaNormalizationJobRunner(
        jobs,
        artifacts,
        MediaNormalizationService(normalizer),
        working_directory=tmp_path,
    )

    await runner.process_batch()

    snapshot = await jobs.get(UPLOAD_ID)
    assert snapshot is not None
    assert snapshot.status is MediaJobStatus.COMPLETED
    assert snapshot.upload.raw_deleted_at == NOW
    assert {artifact.role for artifact in snapshot.artifacts} == set(MediaArtifactRole)
    assert snapshot.upload.source_object_key not in artifacts.objects
    assert all(artifact.object_key.endswith(artifact.sha256) for artifact in snapshot.artifacts)
    report = next(artifact for artifact in snapshot.artifacts if artifact.role is MediaArtifactRole.REPORT)
    report_payload = json.loads(artifacts.objects[report.object_key])
    assert report_payload["kind"] == "video"
    assert report_payload["actions"] == ["video_transcoded"]
    assert not any(tmp_path.iterdir())
    assert all(not directory.exists() for directory in normalizer.seen_directories)

    await jobs.submit(submission)
    assert snapshot.upload.source_object_key not in artifacts.objects


async def test_terminal_validation_failure_cleans_raw_while_retryable_failure_keeps_it(tmp_path: Path) -> None:
    for failure, expected_status, raw_exists in (
        (InvalidMediaError(MediaFailureReason.PROBE_INVALID), MediaJobStatus.DEAD, False),
        (RuntimeError("transient"), MediaJobStatus.PENDING, True),
    ):
        artifacts = MemoryArtifacts()
        repository = MemoryMediaJobs()
        jobs = MediaNormalizationJobService(repository, artifacts, max_attempts=2)
        upload_id = uuid4()
        await jobs.submit(
            MediaUploadSubmission(
                draft_id=DRAFT_ID,
                kind=MediaKind.IMAGE,
                source=b"raw-image",
                source_content_type="image/jpeg",
                upload_id=upload_id,
            )
        )
        normalizer = WritingNormalizer(MediaKind.IMAGE, failure=failure)
        runner = MediaNormalizationJobRunner(
            jobs,
            artifacts,
            MediaNormalizationService(normalizer),
            working_directory=tmp_path,
        )

        await runner.process_batch()

        snapshot = await jobs.get(upload_id)
        assert snapshot is not None
        assert snapshot.status is expected_status
        assert snapshot.attempts == 1
        assert (snapshot.upload.source_object_key in artifacts.objects) is raw_exists
        assert not any(tmp_path.iterdir())
