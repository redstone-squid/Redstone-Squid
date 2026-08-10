"""Durable media upload and normalization job orchestration."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from whenever import Instant

from squid.artifacts import ArtifactStore
from squid.core.errors import SquidError
from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.services import MediaNormalizationService
from squid.media.domain import (
    MediaArtifact,
    MediaKind,
    MediaLimits,
    MediaNormalizationReport,
    MediaProbe,
)
from squid.media.errors import InvalidMediaError, MediaLimitExceededError, MediaProcessingError

logger = logging.getLogger(__name__)

MAX_MEDIA_JOB_CLAIM = 32
MAX_MEDIA_JOB_CLEANUP = 500
DEFAULT_MEDIA_JOB_ATTEMPTS = 3


class MediaJobStatus(StrEnum):
    """Stable durable states for one normalization request."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    DEAD = "dead"


class MediaArtifactRole(StrEnum):
    """The purpose of a durable artifact produced by normalization."""

    OUTPUT = "output"
    POSTER = "poster"
    REPORT = "report"


@dataclass(frozen=True, slots=True)
class MediaUploadSubmission:
    """Attacker-controlled source bytes and server-owned upload identity."""

    draft_id: UUID
    kind: MediaKind
    source: bytes
    source_content_type: str
    strip_audio: bool = False
    upload_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.draft_id.int == 0 or (self.upload_id is not None and self.upload_id.int == 0):
            msg = "Media upload and draft identifiers cannot be nil UUIDs."
            raise ValueError(msg)
        if not self.source:
            msg = "Media uploads cannot be empty."
            raise ValueError(msg)
        _require_content_type(self.source_content_type)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = "Image uploads cannot request audio removal."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MediaUploadMetadata:
    """Immutable metadata for a raw upload staged in object storage."""

    id: UUID
    draft_id: UUID
    kind: MediaKind
    source_content_type: str
    source_byte_size: int
    source_sha256: str
    source_object_key: str
    strip_audio: bool
    created_at: Instant | None = None
    raw_deleted_at: Instant | None = None

    def __post_init__(self) -> None:
        if self.id.int == 0 or self.draft_id.int == 0:
            msg = "Media upload identifiers cannot be nil UUIDs."
            raise ValueError(msg)
        if self.source_byte_size <= 0:
            msg = "Media source byte size must be positive."
            raise ValueError(msg)
        _require_content_type(self.source_content_type)
        _require_sha256(self.source_sha256)
        _require_object_key(self.source_object_key)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = "Image uploads cannot request audio removal."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class StoredMediaArtifact:
    """Durable metadata for content-addressed normalized content."""

    role: MediaArtifactRole
    object_key: str
    content_type: str
    byte_size: int
    sha256: str
    width: int | None
    height: int | None

    def __post_init__(self) -> None:
        _require_object_key(self.object_key)
        _require_sha256(self.sha256)
        _require_content_type(self.content_type)
        if self.byte_size <= 0:
            msg = "Media artifact byte size must be positive."
            raise ValueError(msg)
        if (self.width is None) != (self.height is None):
            msg = "Media artifact dimensions must either both be present or both be absent."
            raise ValueError(msg)
        if self.width is not None and (self.width <= 0 or self.height is None or self.height <= 0):
            msg = "Media artifact dimensions must be positive."
            raise ValueError(msg)
        if self.role is MediaArtifactRole.REPORT and self.width is not None:
            msg = "Normalization reports do not have pixel dimensions."
            raise ValueError(msg)
        if self.role is not MediaArtifactRole.REPORT and self.width is None:
            msg = "Visual media artifacts require pixel dimensions."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ClaimedMediaJob:
    """One normalization request fenced by a unique worker claim token."""

    upload: MediaUploadMetadata
    attempts: int
    claimed_at: Instant
    claim_token: UUID

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.claim_token.int == 0:
            msg = "Claimed media job metadata is invalid."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MediaJobSnapshot:
    """Current durable state and persisted outputs for one upload."""

    upload: MediaUploadMetadata
    status: MediaJobStatus
    attempts: int
    available_at: Instant
    claimed_at: Instant | None
    claim_token: UUID | None
    completed_at: Instant | None
    dead_at: Instant | None
    last_error: str | None
    artifacts: tuple[StoredMediaArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaJobFailureOutcome:
    """Result of a claim-fenced failure transition."""

    applied: bool
    dead: bool


@dataclass(frozen=True, slots=True)
class MediaEnqueueOutcome:
    """Whether registration created a job and the durable state now in effect."""

    created: bool
    status: MediaJobStatus


@dataclass(frozen=True, slots=True)
class TerminalMediaSource:
    """A raw object still awaiting deletion after a terminal job transition."""

    upload_id: UUID
    object_key: str


class MediaUploadConflictError(RuntimeError):
    """An upload UUID was retried with different immutable metadata."""

    def __init__(
        self,
        upload_id: UUID,
        *,
        existing_source_object_key: str,
        existing_status: MediaJobStatus,
    ) -> None:
        super().__init__(f"Media upload {upload_id} already exists with different metadata.")
        self.upload_id = upload_id
        self.existing_source_object_key = existing_source_object_key
        self.existing_status = existing_status


class MediaJobSourceError(RuntimeError):
    """A queued raw object is absent, oversized, or no longer matches its metadata."""


class MediaJobArtifactError(RuntimeError):
    """Object storage did not confirm a content-addressed normalized artifact."""


class MediaJobRepository(Protocol):
    """Durable metadata and claim-fenced queue operations."""

    async def enqueue(self, upload: MediaUploadMetadata) -> MediaEnqueueOutcome: ...

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None: ...

    async def claim(self, *, limit: int) -> Sequence[ClaimedMediaJob]: ...

    async def complete(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> bool: ...

    async def fail(
        self,
        job: ClaimedMediaJob,
        error: str,
        *,
        max_attempts: int,
        terminal: bool,
    ) -> MediaJobFailureOutcome: ...

    async def terminal_sources(self, *, limit: int) -> Sequence[TerminalMediaSource]: ...

    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool: ...


class MediaNormalizationJobService:
    """Stage bounded raw uploads and coordinate durable queue transitions."""

    def __init__(
        self,
        repository: MediaJobRepository,
        artifacts: ArtifactStore,
        *,
        limits: MediaLimits | None = None,
        max_attempts: int = DEFAULT_MEDIA_JOB_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            msg = "Media normalization attempts must be positive."
            raise ValueError(msg)
        self._repository = repository
        self._artifacts = artifacts
        self._limits = limits or MediaLimits()
        self._max_attempts = max_attempts

    @property
    def limits(self) -> MediaLimits:
        """Return the same source and output limits enforced by the worker."""
        return self._limits

    async def submit(self, submission: MediaUploadSubmission) -> UUID:
        """Stage a raw object and idempotently enqueue its immutable metadata."""
        if len(submission.source) > self._limits.max_source_bytes:
            msg = f"Media upload exceeds the {self._limits.max_source_bytes}-byte source limit."
            raise ValueError(msg)
        upload_id = submission.upload_id or uuid4()
        digest = hashlib.sha256(submission.source).hexdigest()
        object_key = f"media/raw/{upload_id}/{digest}"
        stored = await self._artifacts.put(
            object_key,
            submission.source,
            content_type=submission.source_content_type.strip(),
        )
        if stored.byte_size != len(submission.source) or stored.sha256 not in {None, digest}:
            await self._artifacts.delete(object_key)
            msg = "Object storage did not confirm the staged media upload."
            raise MediaJobArtifactError(msg)
        upload = MediaUploadMetadata(
            id=upload_id,
            draft_id=submission.draft_id,
            kind=submission.kind,
            source_content_type=submission.source_content_type.strip(),
            source_byte_size=len(submission.source),
            source_sha256=digest,
            source_object_key=object_key,
            strip_audio=submission.strip_audio,
        )
        try:
            outcome = await self._repository.enqueue(upload)
        except MediaUploadConflictError as error:
            same_key = hmac.compare_digest(error.existing_source_object_key, object_key)
            if not same_key or error.existing_status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
                await self._artifacts.delete(object_key)
            if same_key and error.existing_status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
                await self._repository.mark_source_deleted(TerminalMediaSource(upload_id, object_key))
            raise
        if outcome.status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
            source = TerminalMediaSource(upload_id, object_key)
            await self._artifacts.delete(object_key)
            await self._repository.mark_source_deleted(source)
        return upload_id

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return await self._repository.get(upload_id)

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedMediaJob]:
        if not 1 <= limit <= MAX_MEDIA_JOB_CLAIM:
            msg = f"Media job claim limit must be between 1 and {MAX_MEDIA_JOB_CLAIM}."
            raise ValueError(msg)
        return await self._repository.claim(limit=limit)

    async def complete(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> bool:
        return await self._repository.complete(job, artifacts)

    async def fail(
        self,
        job: ClaimedMediaJob,
        error: Exception,
        *,
        terminal: bool,
    ) -> MediaJobFailureOutcome:
        message = str(error) if isinstance(error, SquidError | MediaJobSourceError) else type(error).__name__
        return await self._repository.fail(
            job,
            message,
            max_attempts=self._max_attempts,
            terminal=terminal,
        )

    async def terminal_sources(self, *, limit: int = 100) -> Sequence[TerminalMediaSource]:
        if not 1 <= limit <= MAX_MEDIA_JOB_CLEANUP:
            msg = f"Media source cleanup limit must be between 1 and {MAX_MEDIA_JOB_CLEANUP}."
            raise ValueError(msg)
        return await self._repository.terminal_sources(limit=limit)

    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool:
        return await self._repository.mark_source_deleted(source)


class MediaNormalizationJobRunner:
    """Normalize claimed uploads in private directories and publish verified outputs."""

    def __init__(
        self,
        jobs: MediaNormalizationJobService,
        artifacts: ArtifactStore,
        normalization: MediaNormalizationService,
        *,
        working_directory: Path | None = None,
    ) -> None:
        if jobs.limits != normalization.limits:
            msg = "Media queue and normalizer limits must match."
            raise ValueError(msg)
        self._jobs = jobs
        self._artifacts = artifacts
        self._normalization = normalization
        self._working_directory = working_directory

    async def process_batch(self, *, limit: int = 8) -> None:
        """Claim and process a bounded batch, including overdue raw-object cleanup."""
        await self.cleanup_terminal_sources()
        claimed = await self._jobs.claim(limit=limit)
        try:
            await asyncio.gather(*(self._process(job) for job in claimed))
        finally:
            await self.cleanup_terminal_sources()

    async def cleanup_terminal_sources(self, *, limit: int = 100) -> None:
        """Retry idempotent deletion of raw objects committed to terminal states."""
        for source in await self._jobs.terminal_sources(limit=limit):
            try:
                await self._artifacts.delete(source.object_key)
                await self._jobs.mark_source_deleted(source)
            except Exception:
                logger.exception(
                    "Media raw-object cleanup failed",
                    extra={"squid.media.upload_id": str(source.upload_id)},
                )

    async def _process(self, job: ClaimedMediaJob) -> None:
        try:
            source = await self._load_source(job)
            artifacts = await self._normalize_and_store(job, source)
            await self._jobs.complete(job, artifacts)
        except Exception as error:
            await self._jobs.fail(job, error, terminal=_is_terminal(error))

    async def _load_source(self, job: ClaimedMediaJob) -> bytes:
        try:
            source = await self._artifacts.get(
                job.upload.source_object_key,
                max_bytes=self._jobs.limits.max_source_bytes,
            )
        except ValueError as error:
            msg = "The queued raw media object exceeds its source limit."
            raise MediaJobSourceError(msg) from error
        if source is None:
            msg = "The queued raw media object is missing."
            raise MediaJobSourceError(msg)
        digest = hashlib.sha256(source).hexdigest()
        if len(source) != job.upload.source_byte_size or not hmac.compare_digest(digest, job.upload.source_sha256):
            msg = "The queued raw media object no longer matches its immutable metadata."
            raise MediaJobSourceError(msg)
        return source

    async def _normalize_and_store(
        self,
        job: ClaimedMediaJob,
        source: bytes,
    ) -> tuple[StoredMediaArtifact, ...]:
        with tempfile.TemporaryDirectory(prefix="squid-media-", dir=self._working_directory) as temporary_name:
            temporary = Path(temporary_name)
            temporary.chmod(0o700)
            source_path = temporary / "source"
            await asyncio.to_thread(_write_private, source_path, source)
            output_path = temporary / ("normalized.png" if job.upload.kind is MediaKind.IMAGE else "normalized.mp4")
            poster_path = temporary / "poster.jpg" if job.upload.kind is MediaKind.VIDEO else None
            result = await self._normalization.normalize(
                MediaNormalizationRequest(
                    kind=job.upload.kind,
                    source_path=source_path,
                    output_path=output_path,
                    poster_path=poster_path,
                    strip_audio=job.upload.strip_audio,
                )
            )
            output = await asyncio.to_thread(
                _read_verified,
                result.output_path,
                result.report.output,
                self._jobs.limits.max_output_bytes,
            )
            stored = [
                await self._store_artifact(
                    MediaArtifactRole.OUTPUT,
                    output,
                    result.report.output.content_type,
                    result.report.output.sha256,
                    width=result.report.output.width,
                    height=result.report.output.height,
                )
            ]
            if result.report.poster is not None:
                if result.poster_path is None:
                    msg = "A video normalization result omitted its poster path."
                    raise MediaJobArtifactError(msg)
                poster = await asyncio.to_thread(
                    _read_verified,
                    result.poster_path,
                    result.report.poster,
                    self._jobs.limits.max_output_bytes,
                )
                stored.append(
                    await self._store_artifact(
                        MediaArtifactRole.POSTER,
                        poster,
                        result.report.poster.content_type,
                        result.report.poster.sha256,
                        width=result.report.poster.width,
                        height=result.report.poster.height,
                    )
                )
            report = _encode_report(result.report)
            report_digest = hashlib.sha256(report).hexdigest()
            stored.append(
                await self._store_artifact(
                    MediaArtifactRole.REPORT,
                    report,
                    "application/json",
                    report_digest,
                    width=None,
                    height=None,
                )
            )
            return tuple(stored)

    async def _store_artifact(
        self,
        role: MediaArtifactRole,
        data: bytes,
        content_type: str,
        digest: str,
        *,
        width: int | None,
        height: int | None,
    ) -> StoredMediaArtifact:
        namespace = {
            MediaArtifactRole.OUTPUT: "normalized",
            MediaArtifactRole.POSTER: "posters",
            MediaArtifactRole.REPORT: "reports",
        }[role]
        object_key = f"media/{namespace}/{digest[:2]}/{digest}"
        metadata = await self._artifacts.put(object_key, data, content_type=content_type)
        if metadata.byte_size != len(data) or metadata.sha256 not in {None, digest}:
            msg = "Object storage did not confirm a normalized media artifact."
            raise MediaJobArtifactError(msg)
        return StoredMediaArtifact(
            role=role,
            object_key=object_key,
            content_type=content_type,
            byte_size=len(data),
            sha256=digest,
            width=width,
            height=height,
        )


def _is_terminal(error: Exception) -> bool:
    return isinstance(error, InvalidMediaError | MediaLimitExceededError | MediaProcessingError | MediaJobSourceError)


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_verified(path: Path, expected: MediaArtifact, limit: int) -> bytes:
    if expected.byte_size > limit:
        msg = "A normalized media artifact exceeds its output limit."
        raise MediaJobArtifactError(msg)
    try:
        with path.open("rb") as stream:
            data = stream.read(expected.byte_size + 1)
    except OSError as error:
        msg = "A normalized media artifact cannot be read."
        raise MediaJobArtifactError(msg) from error
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != expected.byte_size or not hmac.compare_digest(digest, expected.sha256):
        msg = "A normalized media artifact does not match its report."
        raise MediaJobArtifactError(msg)
    return data


def _encode_report(report: MediaNormalizationReport) -> bytes:
    payload = {
        "schema_version": 1,
        "kind": report.kind.value,
        "source_bytes": report.source_bytes,
        "input_probe": _probe_payload(report.input_probe),
        "output_probe": _probe_payload(report.output_probe),
        "output": _artifact_payload(report.output),
        "poster": None if report.poster is None else _artifact_payload(report.poster),
        "actions": [action.value for action in report.actions],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _probe_payload(probe: MediaProbe) -> dict[str, object]:
    return {
        "container_names": list(probe.container_names),
        "video_codec": probe.video_codec,
        "width": probe.width,
        "height": probe.height,
        "frame_rate_numerator": probe.frame_rate_numerator,
        "frame_rate_denominator": probe.frame_rate_denominator,
        "duration_milliseconds": probe.duration_milliseconds,
        "audio_codec": probe.audio_codec,
        "frame_count": probe.frame_count,
    }


def _artifact_payload(artifact: MediaArtifact) -> dict[str, object]:
    return {
        "content_type": artifact.content_type,
        "byte_size": artifact.byte_size,
        "sha256": artifact.sha256,
        "width": artifact.width,
        "height": artifact.height,
    }


def _require_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        msg = "Media SHA-256 values must be lowercase hexadecimal."
        raise ValueError(msg)


def _require_object_key(value: str) -> None:
    normalized = PurePosixPath(value)
    if normalized.is_absolute() or not normalized.parts or any(part in {"", ".", ".."} for part in normalized.parts):
        msg = "Media object keys must be non-empty relative paths without traversal."
        raise ValueError(msg)


def _require_content_type(value: str) -> None:
    if value != value.strip() or not value or len(value) > 255 or any(ord(character) < 32 for character in value):
        msg = "Media content types must be 1-255 printable characters without surrounding whitespace."
        raise ValueError(msg)
