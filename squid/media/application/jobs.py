"""Durable media upload and normalization job orchestration."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import stat
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from whenever import Instant

from squid.artifacts import ArtifactStore
from squid.core.concurrency import run_all_settled, task_group
from squid.core.errors import InvalidStateError, SquidError, ValidationError
from squid.core.i18n import tr
from squid.media.application.commands import MediaNormalizationRequest
from squid.media.application.services import MediaNormalizationService
from squid.media.domain import (
    MediaArtifact,
    MediaKind,
    MediaLimits,
    MediaNormalizationReport,
    MediaProbe,
)
from squid.media.errors import (
    InvalidMediaError,
    MediaArtifactCleanupInProgressError,
    MediaDraftNotFoundError,
    MediaDraftStateConflictError,
    MediaJobArtifactError,
    MediaJobClaimLostError,
    MediaJobSourceError,
    MediaLimitExceededError,
    MediaProcessingError,
    MediaUploadConflictError,
)

logger = logging.getLogger(__name__)

MAX_MEDIA_JOB_CLAIM = 32
MAX_MEDIA_JOB_CLEANUP = 500
DEFAULT_MEDIA_JOB_ATTEMPTS = 3
MEDIA_JOB_HEARTBEAT_INTERVAL_SECONDS = 30.0
MEDIA_ARTIFACT_PUBLICATION_LEASE = timedelta(hours=48)
"""How long a lost worker's object-store publish phase blocks cleanup of its keys.

Sized to outlast the worst case: three sequential objects per video, each allowed an initial
attempt plus ten retries with 60-second connect and one-hour read timeouts, is under 34 hours.
Live workers renew the lease on every heartbeat, so only a dead one ever runs it down.
"""
MEDIA_ARTIFACT_CLEANUP_CLAIM = timedelta(hours=24)
"""How long a cleanup claim on an object key stays exclusive after the claiming worker is lost."""


class MediaJobStatus(StrEnum):
    """Persisted job states; the string values are stored in `media_normalization_jobs.status`."""

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    DEAD = "dead"
    DISCARDED = "discarded"


class MediaArtifactRole(StrEnum):
    """Which file an artifact is: the normalized output, the video poster, or the JSON normalization report."""

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
            msg = tr(t"Media upload and draft identifiers cannot be nil UUIDs.")
            raise ValidationError(msg)
        if not self.source:
            msg = tr(t"Media uploads cannot be empty.")
            raise ValidationError(msg)
        _require_content_type(self.source_content_type)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = tr(t"Image uploads cannot request audio removal.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class StagedMediaUploadSubmission:
    """A private regular file staged by a streaming transport."""

    draft_id: UUID
    kind: MediaKind
    source_path: Path
    source_content_type: str
    strip_audio: bool = False
    upload_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.draft_id.int == 0 or (self.upload_id is not None and self.upload_id.int == 0):
            msg = tr(t"Media upload and draft identifiers cannot be nil UUIDs.")
            raise ValidationError(msg)
        _require_content_type(self.source_content_type)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = tr(t"Image uploads cannot request audio removal.")
            raise ValidationError(msg)


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
            msg = tr(t"Media upload identifiers cannot be nil UUIDs.")
            raise ValidationError(msg)
        if self.source_byte_size <= 0:
            msg = tr(t"Media source byte size must be positive.")
            raise ValidationError(msg)
        _require_content_type(self.source_content_type)
        _require_sha256(self.source_sha256)
        _require_object_key(self.source_object_key)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = tr(t"Image uploads cannot request audio removal.")
            raise ValidationError(msg)


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
            msg = tr(t"Media artifact byte size must be positive.")
            raise ValidationError(msg)
        if (self.width is None) != (self.height is None):
            msg = tr(t"Media artifact dimensions must either both be present or both be absent.")
            raise ValidationError(msg)
        if self.width is not None and (self.width <= 0 or self.height is None or self.height <= 0):
            msg = tr(t"Media artifact dimensions must be positive.")
            raise ValidationError(msg)
        if self.role is MediaArtifactRole.REPORT and self.width is not None:
            msg = tr(t"Normalization reports do not have pixel dimensions.")
            raise ValidationError(msg)
        if self.role is not MediaArtifactRole.REPORT and self.width is None:
            msg = tr(t"Visual media artifacts require pixel dimensions.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class ClaimedMediaJob:
    """One normalization request fenced by a unique worker claim token."""

    upload: MediaUploadMetadata
    attempts: int
    claimed_at: Instant
    claim_token: UUID

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.claim_token.int == 0:
            msg = tr(t"Claimed media job metadata is invalid.")
            raise ValidationError(msg)


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
    discarded_at: Instant | None
    last_error: str | None
    artifacts: tuple[StoredMediaArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaJobFailureOutcome:
    """Result of a claim-fenced failure transition."""

    applied: bool
    dead: bool


@dataclass(frozen=True, slots=True)
class MediaArtifactCleanupOutcome:
    """Counts from one durable normalized-artifact cleanup pass."""

    attempted: int
    deleted: int
    failed: int
    publishers_active: bool = False


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


class MediaJobRepository(Protocol):
    """Durable upload metadata and a claim-token queue; `discard` withdraws one upload from its draft.

    Every transition that takes a `ClaimedMediaJob` is fenced on its claim token and reports `False`
    instead of acting when the token is no longer current.
    """

    async def enqueue(self, upload: MediaUploadMetadata, limits: MediaLimits) -> MediaEnqueueOutcome:
        """Persist `upload` with a pending job, or accept a byte-identical retry of the same id.

        Raises:
            MediaUploadConflictError: The id exists with different metadata.
            MediaDraftNotFoundError: The draft row is gone.
            MediaDraftStateConflictError: The draft is not editable.
            MediaLimitExceededError: The draft's active counts or source bytes would exceed `limits`.
        """
        ...

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        """Current state and persisted artifacts, or `None` for an unknown upload."""
        ...

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]:
        """Every upload for the draft in creation order, terminal ones included."""
        ...

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        """Mark the upload discarded, invalidating any live claim; idempotent.

        Returns `False` when the upload does not belong to `draft_id`. Raises `MediaDraftNotFoundError`
        or `MediaDraftStateConflictError` when the draft is gone or not editable.
        """
        ...

    async def claim(self, *, limit: int) -> Sequence[ClaimedMediaJob]:
        """Lease up to `limit` jobs that are pending or whose claim outlived the visibility timeout.

        Each row gets a fresh claim token, so a reclaimed job's previous holder fails every fenced call.
        """
        ...

    async def heartbeat(self, job: ClaimedMediaJob) -> bool:
        """Renew the claim and every publication lease it holds; `False` when the claim is not current."""
        ...

    async def defer(self, job: ClaimedMediaJob, *, until: Instant) -> bool:
        """Return the job to pending until `until` without counting an attempt, dropping its publication leases."""
        ...

    async def complete(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
        limits: MediaLimits,
    ) -> bool:
        """Record `artifacts` and complete the job in one transaction; `False` when the claim is not current.

        Raises:
            MediaLimitExceededError: The draft's completed output bytes would exceed `limits`.
            MediaArtifactCleanupInProgressError: An artifact key is mid-deletion; retry after `retry_at`.
            ValueError: `artifacts` lacks or duplicates a role the upload's kind requires.
        """
        ...

    async def fail(
        self,
        job: ClaimedMediaJob,
        error: str,
        *,
        max_attempts: int,
        terminal: bool,
    ) -> MediaJobFailureOutcome:
        """Release the job for a backed-off retry, or mark it dead when `terminal` or attempts reach `max_attempts`.

        `error` is truncated to 4000 characters. `applied` is `False` when the claim is not current.
        """
        ...

    async def terminal_sources(self, *, limit: int) -> Sequence[TerminalMediaSource]:
        """Raw objects of completed, dead, or discarded jobs not yet confirmed deleted, oldest first."""
        ...

    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool:
        """Confirm the raw object's deletion; `False` unless the job is terminal and the key still matches."""
        ...

    async def track_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> bool:
        """Register the artifacts' object keys and lease each to this claim before any bytes are written.

        Returns `False` when the claim is not current; the keys are still registered so cleanup can find
        whatever the caller already stored.

        Raises:
            MediaArtifactCleanupInProgressError: A key is mid-deletion; retry after `retry_at`.
            ValueError: A key is registered with a different digest or size, or the roles are wrong.
        """
        ...

    async def release_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None:
        """Drop this claim's publication leases on `artifacts`; other claims' leases on the same keys stay."""
        ...

    async def cleanup_artifacts(
        self,
        delete: Callable[[str], Awaitable[None]],
        *,
        limit: int,
    ) -> MediaArtifactCleanupOutcome:
        """Delete up to `limit` due, unreferenced, unleased objects through `delete`, outside any transaction.

        Each key is claimed with a token first and its outcome acknowledged against that token, so a
        concurrent cleaner or a crash costs a repeated idempotent delete, never a lost object.
        """
        ...


class MediaNormalizationJobService:
    """Stage raw uploads in object storage and drive the durable queue; `discard` withdraws one upload.

    Every staging path deletes the raw object again when the queue rejects it or already holds a
    terminal result for the same id, so a failed submit leaves no orphan in storage.
    """

    def __init__(
        self,
        repository: MediaJobRepository,
        artifacts: ArtifactStore,
        *,
        limits: MediaLimits | None = None,
        max_attempts: int = DEFAULT_MEDIA_JOB_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            msg = tr(t"Media normalization attempts must be positive.")
            raise InvalidStateError(msg)
        self._repository = repository
        self._artifacts = artifacts
        self._limits = limits or MediaLimits()
        self._max_attempts = max_attempts

    @property
    def limits(self) -> MediaLimits:
        """Must equal the worker's `MediaNormalizationService.limits`; the runner refuses to start otherwise."""
        return self._limits

    async def submit(self, submission: MediaUploadSubmission) -> UUID:
        """Stage the in-memory source and enqueue it; retrying with the same `upload_id` and bytes is a no-op.

        Raises:
            ValidationError: The source exceeds `limits.max_source_bytes`.
            MediaJobArtifactError: Object storage reports a different size or digest than was sent.
            MediaUploadConflictError: `upload_id` exists with different metadata.
            MediaDraftNotFoundError: The draft row is gone.
            MediaDraftStateConflictError: The draft is not editable.
            MediaLimitExceededError: The draft's active media would exceed `limits`.
        """
        if len(submission.source) > self._limits.max_source_bytes:
            limit = self._limits.max_source_bytes
            raise ValidationError(tr(t"Media upload exceeds the {limit}-byte source limit."))
        digest = hashlib.sha256(submission.source).hexdigest()
        upload_id = submission.upload_id or uuid4()
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
        return await self._register(
            MediaUploadMetadata(
                id=upload_id,
                draft_id=submission.draft_id,
                kind=submission.kind,
                source_content_type=submission.source_content_type.strip(),
                source_byte_size=len(submission.source),
                source_sha256=digest,
                source_object_key=object_key,
                strip_audio=submission.strip_audio,
            )
        )

    async def submit_staged(self, submission: StagedMediaUploadSubmission) -> UUID:
        """Like `submit`, streaming a regular file from disk instead of holding it in memory.

        Also raises `ValidationError` when the path is not a regular file, is empty, or changes while
        being hashed.
        """
        byte_size, digest = await asyncio.to_thread(
            _staged_source_metadata,
            submission.source_path,
            self._limits.max_source_bytes,
        )
        upload_id = submission.upload_id or uuid4()
        object_key = f"media/raw/{upload_id}/{digest}"
        stored = await self._artifacts.put_path(
            object_key,
            submission.source_path,
            content_type=submission.source_content_type.strip(),
            max_bytes=self._limits.max_source_bytes,
        )
        if stored.byte_size != byte_size or stored.sha256 not in {None, digest}:
            await self._artifacts.delete(object_key)
            msg = "Object storage did not confirm the staged media upload."
            raise MediaJobArtifactError(msg)
        return await self._register(
            MediaUploadMetadata(
                id=upload_id,
                draft_id=submission.draft_id,
                kind=submission.kind,
                source_content_type=submission.source_content_type.strip(),
                source_byte_size=byte_size,
                source_sha256=digest,
                source_object_key=object_key,
                strip_audio=submission.strip_audio,
            )
        )

    async def _register(self, upload: MediaUploadMetadata) -> UUID:
        """Enqueue `upload`, deleting its raw object when the queue rejects it or is already terminal for the id."""
        object_key = upload.source_object_key
        upload_id = upload.id
        try:
            outcome = await self._repository.enqueue(upload, self._limits)
        except MediaLimitExceededError, MediaDraftNotFoundError, MediaDraftStateConflictError:
            await self._artifacts.delete(object_key)
            raise
        except MediaUploadConflictError as error:
            same_key = hmac.compare_digest(error.existing_source_object_key, object_key)
            if not same_key or error.existing_status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
                await self._artifacts.delete(object_key)
            if same_key and error.existing_status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
                await self._repository.mark_source_deleted(TerminalMediaSource(upload_id, object_key))
            raise
        if outcome.status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD, MediaJobStatus.DISCARDED}:
            source = TerminalMediaSource(upload_id, object_key)
            await self._artifacts.delete(object_key)
            await self._repository.mark_source_deleted(source)
        return upload_id

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        return await self._repository.get(upload_id)

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]:
        """Every upload for the draft in creation order, terminal ones included."""
        return await self._repository.list_for_draft(draft_id)

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        """Withdraw one upload; its raw object is deleted by the next `MediaStorageCleanup` pass, not here.

        Raises `MediaDraftNotFoundError` or `MediaDraftStateConflictError` when the draft is gone or not
        editable.
        """
        return await self._repository.discard(draft_id, upload_id)

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedMediaJob]:
        """Raises `InvalidStateError` unless `1 <= limit <= MAX_MEDIA_JOB_CLAIM`."""
        if not 1 <= limit <= MAX_MEDIA_JOB_CLAIM:
            maximum = MAX_MEDIA_JOB_CLAIM
            raise InvalidStateError(tr(t"Media job claim limit must be between 1 and {maximum}."))
        return await self._repository.claim(limit=limit)

    async def heartbeat(self, job: ClaimedMediaJob) -> bool:
        return await self._repository.heartbeat(job)

    async def defer(self, job: ClaimedMediaJob, *, until: Instant) -> bool:
        return await self._repository.defer(job, until=until)

    async def complete(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> bool:
        return await self._repository.complete(job, artifacts, self._limits)

    async def fail(
        self,
        job: ClaimedMediaJob,
        error: Exception,
        *,
        terminal: bool,
    ) -> MediaJobFailureOutcome:
        """Only Squid errors persist their message; anything else stores just its type name."""
        message = str(error) if isinstance(error, SquidError | MediaJobSourceError) else type(error).__name__
        return await self._repository.fail(
            job,
            message,
            max_attempts=self._max_attempts,
            terminal=terminal,
        )

    async def terminal_sources(self, *, limit: int = 100) -> Sequence[TerminalMediaSource]:
        """Raises `InvalidStateError` unless `1 <= limit <= MAX_MEDIA_JOB_CLEANUP`."""
        if not 1 <= limit <= MAX_MEDIA_JOB_CLEANUP:
            maximum = MAX_MEDIA_JOB_CLEANUP
            raise InvalidStateError(tr(t"Media source cleanup limit must be between 1 and {maximum}."))
        return await self._repository.terminal_sources(limit=limit)

    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool:
        return await self._repository.mark_source_deleted(source)

    async def track_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> bool:
        return await self._repository.track_artifacts(job, artifacts)

    async def release_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None:
        await self._repository.release_artifacts(job, artifacts)

    async def cleanup_artifacts(self, *, limit: int = 100) -> MediaArtifactCleanupOutcome:
        """Raises `InvalidStateError` unless `1 <= limit <= MAX_MEDIA_JOB_CLEANUP`."""
        if not 1 <= limit <= MAX_MEDIA_JOB_CLEANUP:
            maximum = MAX_MEDIA_JOB_CLEANUP
            raise InvalidStateError(tr(t"Media artifact cleanup limit must be between 1 and {maximum}."))
        return await self._repository.cleanup_artifacts(self._artifacts.delete, limit=limit)


class MediaStorageCleanup:
    """Deletes raw objects of terminal jobs and unreferenced normalized objects; each pass logs failures and returns."""

    def __init__(self, jobs: MediaNormalizationJobService, artifacts: ArtifactStore) -> None:
        self._jobs = jobs
        self._artifacts = artifacts

    async def process_batch(self, *, limit: int = 100) -> None:
        await self.cleanup_terminal_sources(limit=limit)
        await self.cleanup_terminal_artifacts(limit=limit)

    async def cleanup_terminal_sources(self, *, limit: int = 100) -> None:
        """One failed deletion is logged and skipped; the rest of the batch still runs."""
        for source in await self._jobs.terminal_sources(limit=limit):
            try:
                await self._artifacts.delete(source.object_key)
                await self._jobs.mark_source_deleted(source)
            except Exception:
                logger.exception(
                    "Media raw-object cleanup failed",
                    extra={"squid.media.upload_id": str(source.upload_id)},
                )

    async def cleanup_terminal_artifacts(self, *, limit: int = 100) -> None:
        outcome = await self._jobs.cleanup_artifacts(limit=limit)
        if outcome.failed:
            logger.warning(
                "Media normalized-artifact cleanup completed with failures",
                extra={
                    "squid.media.cleanup.attempted": outcome.attempted,
                    "squid.media.cleanup.deleted": outcome.deleted,
                    "squid.media.cleanup.failed": outcome.failed,
                },
            )


class MediaNormalizationJobRunner:
    """Normalize claimed uploads in a private temporary directory each and publish the verified outputs.

    Construction raises `InvalidStateError` when `jobs.limits != normalization.limits` or the heartbeat
    interval is not positive.
    """

    def __init__(
        self,
        jobs: MediaNormalizationJobService,
        artifacts: ArtifactStore,
        normalization: MediaNormalizationService,
        *,
        working_directory: Path | None = None,
        cleanup: MediaStorageCleanup | None = None,
        heartbeat_interval_seconds: float = MEDIA_JOB_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if jobs.limits != normalization.limits:
            msg = tr(t"Media queue and normalizer limits must match.")
            raise InvalidStateError(msg)
        if heartbeat_interval_seconds <= 0:
            msg = tr(t"Media job heartbeat interval must be positive.")
            raise InvalidStateError(msg)
        self._jobs = jobs
        self._artifacts = artifacts
        self._normalization = normalization
        self._working_directory = working_directory
        self._cleanup = cleanup or MediaStorageCleanup(jobs, artifacts)
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def process_batch(self, *, limit: int = 8) -> None:
        """Claim up to `limit` jobs and process them concurrently; failures raise only after every job settles."""
        claimed = await self._jobs.claim(limit=limit)
        # Settled rather than cancelled on first failure: a cancelled job never reaches
        # the handler that fails it, so it would hold its claim, heartbeat and temporary
        # directory until the lease expires.
        await run_all_settled([partial(self._process, job) for job in claimed])

    async def cleanup_terminal_sources(self, *, limit: int = 100) -> None:
        await self._cleanup.cleanup_terminal_sources(limit=limit)

    async def cleanup_terminal_artifacts(self, *, limit: int = 100) -> None:
        await self._cleanup.cleanup_terminal_artifacts(limit=limit)

    async def _process(self, job: ClaimedMediaJob) -> None:
        claim_lost = asyncio.Event()
        async with task_group() as heartbeat:
            heartbeat.start_soon(self._maintain_claim, job, claim_lost)
            try:
                await self._process_claim(job, claim_lost)
            finally:
                heartbeat.cancel_scope.cancel()

    async def _process_claim(self, job: ClaimedMediaJob, claim_lost: asyncio.Event) -> None:
        with tempfile.TemporaryDirectory(prefix="squid-media-", dir=self._working_directory) as temporary_name:
            temporary = Path(temporary_name)
            temporary.chmod(0o700)
            source_path = temporary / "source"
            try:
                await self._load_source(job, source_path)
                artifacts = await self._normalize_and_store(job, source_path, temporary, claim_lost)
                await self._require_claim(job, claim_lost)
                try:
                    completed = await self._jobs.complete(job, artifacts)
                finally:
                    await self._jobs.release_artifacts(job, artifacts)
                if not completed:
                    return
            except MediaArtifactCleanupInProgressError as error:
                await self._jobs.defer(job, until=error.retry_at)
            except MediaJobClaimLostError:
                return
            except Exception as error:
                await self._jobs.fail(job, error, terminal=_is_terminal(error))

    async def _maintain_claim(self, job: ClaimedMediaJob, claim_lost: asyncio.Event) -> None:
        """Renew the claim every interval; the first failed or rejected renewal sets `claim_lost` and stops."""
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                current = await self._jobs.heartbeat(job)
            except Exception:
                logger.exception(
                    "Media job heartbeat failed",
                    extra={"squid.media.upload_id": str(job.upload.id)},
                )
                claim_lost.set()
                return
            if not current:
                claim_lost.set()
                return

    async def _require_claim(self, job: ClaimedMediaJob, claim_lost: asyncio.Event) -> None:
        if claim_lost.is_set() or not await self._jobs.heartbeat(job):
            claim_lost.set()
            raise MediaJobClaimLostError

    async def _load_source(self, job: ClaimedMediaJob, destination: Path) -> None:
        try:
            source = await self._artifacts.get_path(
                job.upload.source_object_key,
                destination,
                max_bytes=self._jobs.limits.max_source_bytes,
            )
        except ValueError as error:
            msg = "The queued raw media object exceeds its source limit."
            raise MediaJobSourceError(msg) from error
        if source is None:
            msg = "The queued raw media object is missing."
            raise MediaJobSourceError(msg)
        if (
            source.byte_size != job.upload.source_byte_size
            or source.sha256 is None
            or not hmac.compare_digest(
                source.sha256,
                job.upload.source_sha256,
            )
        ):
            msg = "The queued raw media object no longer matches its immutable metadata."
            raise MediaJobSourceError(msg)

    async def _normalize_and_store(
        self,
        job: ClaimedMediaJob,
        source_path: Path,
        temporary: Path,
        claim_lost: asyncio.Event,
    ) -> tuple[StoredMediaArtifact, ...]:
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
        await self._require_claim(job, claim_lost)
        output = await asyncio.to_thread(
            _read_verified,
            result.output_path,
            result.report.output,
            self._jobs.limits.max_output_bytes,
        )
        prepared: list[tuple[StoredMediaArtifact, bytes]] = [
            (
                self._artifact_metadata(
                    MediaArtifactRole.OUTPUT,
                    output,
                    result.report.output.content_type,
                    result.report.output.sha256,
                    width=result.report.output.width,
                    height=result.report.output.height,
                ),
                output,
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
            prepared.append(
                (
                    self._artifact_metadata(
                        MediaArtifactRole.POSTER,
                        poster,
                        result.report.poster.content_type,
                        result.report.poster.sha256,
                        width=result.report.poster.width,
                        height=result.report.poster.height,
                    ),
                    poster,
                )
            )
        report = _encode_report(result.report)
        report_digest = hashlib.sha256(report).hexdigest()
        prepared.append(
            (
                self._artifact_metadata(
                    MediaArtifactRole.REPORT,
                    report,
                    "application/json",
                    report_digest,
                    width=None,
                    height=None,
                ),
                report,
            )
        )
        artifacts = tuple(artifact for artifact, _ in prepared)
        if not await self._jobs.track_artifacts(job, artifacts):
            raise MediaJobClaimLostError
        try:
            for artifact, data in prepared:
                await self._require_claim(job, claim_lost)
                await self._store_artifact(artifact, data)
                try:
                    await self._require_claim(job, claim_lost)
                except MediaJobClaimLostError:
                    # The object is already in storage; re-register its key so cleanup can find it.
                    await self._jobs.track_artifacts(job, artifacts)
                    raise
        except Exception:
            await self._jobs.release_artifacts(job, artifacts)
            raise
        return artifacts

    @staticmethod
    def _artifact_metadata(
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
        return StoredMediaArtifact(
            role=role,
            object_key=object_key,
            content_type=content_type,
            byte_size=len(data),
            sha256=digest,
            width=width,
            height=height,
        )

    async def _store_artifact(self, artifact: StoredMediaArtifact, data: bytes) -> None:
        metadata = await self._artifacts.put(artifact.object_key, data, content_type=artifact.content_type)
        if metadata.byte_size != len(data) or metadata.sha256 not in {None, artifact.sha256}:
            msg = "Object storage did not confirm a normalized media artifact."
            raise MediaJobArtifactError(msg)


def _is_terminal(error: Exception) -> bool:
    return isinstance(error, InvalidMediaError | MediaLimitExceededError | MediaProcessingError | MediaJobSourceError)


def _staged_source_metadata(path: Path, max_bytes: int) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            msg = tr(t"Media uploads must be staged as regular files.")
            raise ValidationError(msg)
        if initial.st_size <= 0:
            msg = tr(t"Media uploads cannot be empty.")
            raise ValidationError(msg)
        if initial.st_size > max_bytes:
            limit = max_bytes
            raise ValidationError(tr(t"Media upload exceeds the {limit}-byte source limit."))
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            current = os.fstat(stream.fileno())
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
        ):
            msg = tr(t"Media upload changed while it was being staged.")
            raise ValidationError(msg)
        return initial.st_size, digest.hexdigest()
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
        msg = tr(t"Media SHA-256 values must be lowercase hexadecimal.")
        raise ValidationError(msg)


def _require_object_key(value: str) -> None:
    normalized = PurePosixPath(value)
    if normalized.is_absolute() or not normalized.parts or any(part in {"", ".", ".."} for part in normalized.parts):
        msg = tr(t"Media object keys must be non-empty relative paths without traversal.")
        raise ValidationError(msg)


def _require_content_type(value: str) -> None:
    if value != value.strip() or not value or len(value) > 255 or any(ord(character) < 32 for character in value):
        msg = tr(t"Media content types must be 1-255 printable characters without surrounding whitespace.")
        raise ValidationError(msg)
