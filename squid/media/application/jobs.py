"""Retain uploaded images and videos until workers produce normalized files.

MediaNormalizationJobService stores the original upload and queues a job.
MediaNormalizationJobRunner retrieves those bytes, normalizes them in a private
temporary directory, and stores the output plus a processing report. Videos also
produce a JPEG thumbnail, called a poster by the normalizer.

Uploads are identified by UUID; normalized objects are keyed by their content
hash and can be shared. MediaStorageCleanup deletes original uploads after their
jobs finish and removes normalized objects when no retained reference needs them.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

import anyio.to_thread
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
from squid.media.domain.jobs import MediaArtifactRole, MediaJobStatus
from squid.media.errors import (
    InvalidMediaError,
    MediaArtifactCleanupInProgressError,
    MediaDraftNotFoundError,
    MediaDraftRevisionConflictError,
    MediaDraftStateConflictError,
    MediaJobArtifactError,
    MediaJobClaimLostError,
    MediaJobSourceError,
    MediaLimitExceededError,
    MediaProcessingError,
    MediaUploadConflictError,
)
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory
from squid_i18n import md_tr

logger = logging.getLogger(__name__)

MAX_MEDIA_JOB_CLAIM = 32
MAX_MEDIA_JOB_CLEANUP = 500
DEFAULT_MEDIA_JOB_ATTEMPTS = 3
MEDIA_JOB_HEARTBEAT_INTERVAL_SECONDS = 30.0
MEDIA_ARTIFACT_PUBLICATION_LEASE = timedelta(hours=48)
"""Crash-recovery lease covering the object-store publish phase after normalization.

Object storage allows at most ten retries with 60-second connect and one-hour read
timeouts. A video publishes at most three sequential objects; conservatively counting
an initial attempt plus all ten retries for every object takes under thirty-four hours
before SDK backoff, leaving more than fourteen hours of lease margin. Active workers
also renew their claim before and after every publication.
"""
MEDIA_ARTIFACT_CLEANUP_CLAIM = timedelta(hours=24)
"""Recovery window for an object deletion that may still be running after worker loss."""


@dataclass(frozen=True, slots=True)
class MediaDraftUploadAuthorization:
    """Owner and revision proof that persistence must revalidate atomically."""

    owner_account_id: int
    draft_revision: int

    def __post_init__(self) -> None:
        if self.owner_account_id < 1 or self.draft_revision < 0:
            msg = tr(t"media-media-draft-upload = Media draft upload authorization is invalid.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class MediaUploadSubmission:
    """Raw upload bytes, declared content type, and an optional UUID for retries."""

    draft_id: UUID
    kind: MediaKind
    source: bytes
    source_content_type: str
    strip_audio: bool = False
    upload_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.draft_id.int == 0 or (self.upload_id is not None and self.upload_id.int == 0):
            msg = tr(t"media-media-upload-draft = Media upload and draft identifiers cannot be nil UUIDs.")
            raise ValidationError(msg)
        if not self.source:
            msg = tr(t"media-media-uploads-empty = Media uploads cannot be empty.")
            raise ValidationError(msg)
        _require_content_type(self.source_content_type)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = tr(t"media-image-uploads-request = Image uploads cannot request audio removal.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class StagedMediaUploadSubmission:
    """A transport-staged file that the caller must keep private and unchanged until submission ends."""

    draft_id: UUID
    kind: MediaKind
    source_path: Path
    source_content_type: str
    strip_audio: bool = False
    upload_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.draft_id.int == 0 or (self.upload_id is not None and self.upload_id.int == 0):
            msg = tr(t"media-media-upload-draft = Media upload and draft identifiers cannot be nil UUIDs.")
            raise ValidationError(msg)
        _require_content_type(self.source_content_type)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = tr(t"media-image-uploads-request = Image uploads cannot request audio removal.")
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
            msg = tr(t"media-media-upload-identifiers = Media upload identifiers cannot be nil UUIDs.")
            raise ValidationError(msg)
        if self.source_byte_size <= 0:
            msg = tr(t"media-media-source-byte = Media source byte size must be positive.")
            raise ValidationError(msg)
        _require_content_type(self.source_content_type)
        _require_sha256(self.source_sha256)
        _require_object_key(self.source_object_key)
        if self.kind is MediaKind.IMAGE and self.strip_audio:
            msg = tr(t"media-image-uploads-request = Image uploads cannot request audio removal.")
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
            msg = tr(t"media-media-artifact-byte = Media artifact byte size must be positive.")
            raise ValidationError(msg)
        if (self.width is None) != (self.height is None):
            msg = tr(
                t"media-artifact-dimensions-either = Media artifact "
                t"dimensions must either both be present or both be absent."
            )
            raise ValidationError(msg)
        if self.width is not None and (self.width <= 0 or self.height is None or self.height <= 0):
            msg = tr(t"media-media-artifact-dimensions = Media artifact dimensions must be positive.")
            raise ValidationError(msg)
        if self.role is MediaArtifactRole.REPORT and self.width is not None:
            msg = tr(t"media-reports-not-pixel = Normalization reports do not have pixel dimensions.")
            raise ValidationError(msg)
        if self.role is not MediaArtifactRole.REPORT and self.width is None:
            msg = tr(t"media-visual-media-artifacts = Visual media artifacts require pixel dimensions.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class ClaimedMediaJob:
    """A normalization request owned by one worker claim.

    attempts counts recorded failures before this claim, not the number of claims.
    Every state change must match claim_token so a reclaimed job rejects old results.
    """

    upload: MediaUploadMetadata
    attempts: int
    claimed_at: Instant
    claim_token: UUID

    def __post_init__(self) -> None:
        if self.attempts < 0 or self.claim_token.int == 0:
            msg = tr(t"media-claimed-media-job = Claimed media job metadata is invalid.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class MediaJobSnapshot:
    """Job state and stored outputs for one upload.

    A draft listing reports that draft's view: it may show DISCARDED even when
    another draft retains the shared job, and upload.draft_id identifies the listed draft.
    """

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


@dataclass(frozen=True, slots=True)
class ArtifactCleanupClaim:
    """Authority to delete one unreferenced media object."""

    object_key: str
    claim_token: UUID


class MediaJobRepository(Protocol):
    """Durable metadata and claim-fenced queue operations."""

    async def enqueue(
        self,
        upload: MediaUploadMetadata,
        limits: MediaLimits,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> MediaEnqueueOutcome: ...

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None: ...

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]: ...

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool: ...

    async def attach(self, source_id: UUID, target_id: UUID, upload_id: UUID, limits: MediaLimits) -> bool: ...

    async def claim(self, *, limit: int) -> Sequence[ClaimedMediaJob]: ...

    async def heartbeat(self, job: ClaimedMediaJob) -> bool: ...

    async def defer(self, job: ClaimedMediaJob, *, until: Instant) -> bool: ...

    async def complete(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
        limits: MediaLimits,
    ) -> bool: ...

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

    async def track_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> bool: ...

    async def release_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None: ...

    async def claim_artifact_cleanup(self, *, limit: int) -> tuple[ArtifactCleanupClaim, ...]: ...

    async def resolve_artifact_cleanup(
        self, resolutions: Sequence[tuple[ArtifactCleanupClaim, str | None]]
    ) -> None: ...


class MediaNormalizationJobService:
    """Stage raw uploads and commit each queue transition in its own unit of work.

    Object writes happen before registration. Reusing an upload UUID is idempotent
    only when its metadata matches. Workers receive committed claims and must check
    their tokens before publishing objects or recording results.
    """

    def __init__(
        self,
        artifacts: ArtifactStore,
        *,
        uow_factory: UnitOfWorkFactory[MediaUnitOfWork],
        limits: MediaLimits | None = None,
        max_attempts: int = DEFAULT_MEDIA_JOB_ATTEMPTS,
    ) -> None:
        if max_attempts < 1:
            msg = tr(t"media-media-attempts-positive = Media normalization attempts must be positive.")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._artifacts = artifacts
        self._limits = limits or MediaLimits()
        self._max_attempts = max_attempts

    @property
    def limits(self) -> MediaLimits:
        """Return the same source and output limits enforced by the worker."""
        return self._limits

    async def submit(
        self,
        submission: MediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        """Stage a raw object and idempotently enqueue its immutable metadata."""
        return await self._submit(submission, authorization=authorization)

    async def submit_staged(
        self,
        submission: StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> UUID:
        """Stage a bounded regular file without buffering it in application memory."""
        return await self._submit(submission, authorization=authorization)

    async def _submit(
        self,
        submission: MediaUploadSubmission | StagedMediaUploadSubmission,
        *,
        authorization: MediaDraftUploadAuthorization | None,
    ) -> UUID:
        if isinstance(submission, StagedMediaUploadSubmission):
            byte_size, digest = await anyio.to_thread.run_sync(
                _staged_source_metadata,
                submission.source_path,
                self._limits.max_source_bytes,
                abandon_on_cancel=False,
            )
        else:
            byte_size = len(submission.source)
            if byte_size > self._limits.max_source_bytes:
                limit = self._limits.max_source_bytes
                raise ValidationError(
                    md_tr(t"media-media-upload-exceeds = Media upload exceeds the {limit}-byte source limit.")
                )
            digest = hashlib.sha256(submission.source).hexdigest()
        upload_id = submission.upload_id or uuid4()
        object_key = f"media/raw/{upload_id}/{digest}"
        content_type = submission.source_content_type
        if isinstance(submission, StagedMediaUploadSubmission):
            stored = await self._artifacts.put_path(
                object_key, submission.source_path, content_type=content_type, max_bytes=self._limits.max_source_bytes
            )
        else:
            stored = await self._artifacts.put(object_key, submission.source, content_type=content_type)
        if stored.byte_size != byte_size or stored.sha256 not in {None, digest}:
            await self._artifacts.delete(object_key)
            msg = "Object storage did not confirm the staged media upload."
            raise MediaJobArtifactError(msg)
        return await self._register(
            MediaUploadMetadata(
                id=upload_id,
                draft_id=submission.draft_id,
                kind=submission.kind,
                source_content_type=content_type,
                source_byte_size=byte_size,
                source_sha256=digest,
                source_object_key=object_key,
                strip_audio=submission.strip_audio,
            ),
            authorization=authorization,
        )

    async def _register(
        self,
        upload: MediaUploadMetadata,
        *,
        authorization: MediaDraftUploadAuthorization | None,
    ) -> UUID:
        """Register immutable staged metadata and reconcile terminal replays."""
        object_key = upload.source_object_key
        upload_id = upload.id
        try:
            async with self._uow_factory() as uow:
                outcome = await uow.media_jobs.enqueue(upload, self._limits, authorization=authorization)
                await uow.commit()
        except (
            MediaLimitExceededError,
            MediaDraftNotFoundError,
            MediaDraftRevisionConflictError,
            MediaDraftStateConflictError,
        ):
            await self._artifacts.delete(object_key)
            raise
        except MediaUploadConflictError as error:
            same_key = hmac.compare_digest(error.existing_source_object_key, object_key)
            if not same_key or error.existing_status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
                await self._artifacts.delete(object_key)
            if same_key and error.existing_status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD}:
                await self.mark_source_deleted(TerminalMediaSource(upload_id, object_key))
            raise
        if outcome.status in {MediaJobStatus.COMPLETED, MediaJobStatus.DEAD, MediaJobStatus.DISCARDED}:
            source = TerminalMediaSource(upload_id, object_key)
            await self._artifacts.delete(object_key)
            await self.mark_source_deleted(source)
        return upload_id

    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        async with self._uow_factory() as uow:
            return await uow.media_jobs.get(upload_id)

    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]:
        """List retained upload state for one draft in creation order."""
        async with self._uow_factory() as uow:
            return await uow.media_jobs.list_for_draft(draft_id)

    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        """Discard a draft's upload reference, retaining the shared job if another draft needs it."""
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.discard(draft_id, upload_id)
            await uow.commit()
            return result

    async def attach(self, source_id: UUID, target_id: UUID, upload_id: UUID) -> bool:
        """Attach an upload to another draft of the same owner, enforcing its capacity limits."""
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.attach(source_id, target_id, upload_id, self._limits)
            await uow.commit()
            return result

    async def claim(self, *, limit: int = 8) -> Sequence[ClaimedMediaJob]:
        """Commit claims for ready or abandoned work before returning it to workers."""
        async with self._uow_factory() as uow:
            if not 1 <= limit <= MAX_MEDIA_JOB_CLAIM:
                maximum = MAX_MEDIA_JOB_CLAIM
                raise InvalidStateError(
                    md_tr(t"media-media-job-claim = Media job claim limit must be between 1 and {maximum}.")
                )
            result = await uow.media_jobs.claim(limit=limit)
            await uow.commit()
            return result

    async def heartbeat(self, job: ClaimedMediaJob) -> bool:
        """Renew a current media claim and every publication lease it owns."""
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.heartbeat(job)
            await uow.commit()
            return result

    async def defer(self, job: ClaimedMediaJob, *, until: Instant) -> bool:
        """Release a current claim without consuming a normalization attempt."""
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.defer(job, until=until)
            await uow.commit()
            return result

    async def complete(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> bool:
        """Record published artifacts, returning False if the job claim is no longer current."""
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.complete(job, artifacts, self._limits)
            await uow.commit()
            return result

    async def fail(
        self,
        job: ClaimedMediaJob,
        error: Exception,
        *,
        terminal: bool,
    ) -> MediaJobFailureOutcome:
        async with self._uow_factory() as uow:
            message = str(error) if isinstance(error, SquidError | MediaJobSourceError) else type(error).__name__
            result = await uow.media_jobs.fail(
                job,
                message,
                max_attempts=self._max_attempts,
                terminal=terminal,
            )
            await uow.commit()
            return result

    async def terminal_sources(self, *, limit: int = 100) -> Sequence[TerminalMediaSource]:
        async with self._uow_factory() as uow:
            if not 1 <= limit <= MAX_MEDIA_JOB_CLEANUP:
                maximum = MAX_MEDIA_JOB_CLEANUP
                raise InvalidStateError(
                    md_tr(t"media-media-source-cleanup = Media source cleanup limit must be between 1 and {maximum}.")
                )
            return await uow.media_jobs.terminal_sources(limit=limit)

    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool:
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.mark_source_deleted(source)
            await uow.commit()
            return result

    async def track_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> bool:
        """Register possible object keys before publishing bytes to storage."""
        async with self._uow_factory() as uow:
            result = await uow.media_jobs.track_artifacts(job, artifacts)
            await uow.commit()
            return result

    async def release_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None:
        """Release only this claim's per-object publication leases."""
        async with self._uow_factory() as uow:
            await uow.media_jobs.release_artifacts(job, artifacts)
            await uow.commit()

    async def cleanup_artifacts(self, *, limit: int = 100) -> MediaArtifactCleanupOutcome:
        """Claim unreferenced objects for deletion, then record each storage result."""
        if not 1 <= limit <= MAX_MEDIA_JOB_CLEANUP:
            maximum = MAX_MEDIA_JOB_CLEANUP
            raise InvalidStateError(
                md_tr(t"media-media-artifact-cleanup = Media artifact cleanup limit must be between 1 and {maximum}.")
            )
        async with self._uow_factory() as uow:
            claims = await uow.media_jobs.claim_artifact_cleanup(limit=limit)
            await uow.commit()
        resolutions: list[tuple[ArtifactCleanupClaim, str | None]] = []
        deleted = 0
        for claim in claims:
            try:
                await self._artifacts.delete(claim.object_key)
            except Exception as error:
                resolutions.append((claim, type(error).__name__[:4000]))
            else:
                deleted += 1
                resolutions.append((claim, None))
        if resolutions:
            async with self._uow_factory() as uow:
                await uow.media_jobs.resolve_artifact_cleanup(resolutions)
                await uow.commit()
        return MediaArtifactCleanupOutcome(len(claims), deleted, len(claims) - deleted)


class MediaStorageCleanup:
    """Run raw and normalized object cleanup independently of the normalization worker."""

    def __init__(self, jobs: MediaNormalizationJobService, artifacts: ArtifactStore) -> None:
        self._jobs = jobs
        self._artifacts = artifacts

    async def process_batch(self, *, limit: int = 100) -> None:
        """Retry deletion of finished jobs' source files and unreferenced normalized objects."""
        await self.cleanup_terminal_sources(limit=limit)
        await self.cleanup_terminal_artifacts(limit=limit)

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

    async def cleanup_terminal_artifacts(self, *, limit: int = 100) -> None:
        """Retry reference-fenced deletion of normalized objects and reports."""
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
    """Normalize claimed uploads in private directories and publish verified outputs."""

    def __init__(
        self,
        jobs: MediaNormalizationJobService,
        artifacts: ArtifactStore,
        normalization: MediaNormalizationService,
        *,
        working_directory: Path | None = None,
        heartbeat_interval_seconds: float = MEDIA_JOB_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if jobs.limits != normalization.limits:
            msg = tr(t"media-media-queue-normalizer = Media queue and normalizer limits must match.")
            raise InvalidStateError(msg)
        if heartbeat_interval_seconds <= 0:
            msg = tr(t"media-media-job-heartbeat = Media job heartbeat interval must be positive.")
            raise InvalidStateError(msg)
        self._jobs = jobs
        self._artifacts = artifacts
        self._normalization = normalization
        self._working_directory = working_directory
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def process_batch(self, *, limit: int = 8) -> None:
        """Claim and process a bounded batch without coupling work to storage cleanup."""
        claimed = await self._jobs.claim(limit=limit)
        # Let independent jobs finish if one fails. Cancelling siblings would skip
        # their failure transitions and leave their database claims until expiry.
        # Unexpected failures are raised after every job has settled.
        await run_all_settled([partial(self._process, job) for job in claimed])

    async def _process(self, job: ClaimedMediaJob) -> None:
        claim_lost = asyncio.Event()
        async with task_group() as heartbeat:
            heartbeat.start_soon(self._maintain_claim, job, claim_lost, name=f"media-heartbeat-{job.upload.id}")
            try:
                await self._process_claim(job, claim_lost)
            finally:
                # Stop renewal on every exit path, including cancellation and lost claims.
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
        """Renew the claim during normalization and block publication if renewal fails."""
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
        files = [(MediaArtifactRole.OUTPUT, result.output_path, result.report.output)]
        if result.report.poster is not None:
            if result.poster_path is None:
                msg = "A video normalization result omitted its poster path."
                raise MediaJobArtifactError(msg)
            files.append((MediaArtifactRole.VIDEO_THUMBNAIL, result.poster_path, result.report.poster))
        prepared: list[tuple[StoredMediaArtifact, bytes]] = []
        for role, path, facts in files:
            data = await asyncio.to_thread(_read_verified, path, facts, self._jobs.limits.max_output_bytes)
            prepared.append(
                (
                    self._artifact_metadata(
                        role, data, facts.content_type, facts.sha256, width=facts.width, height=facts.height
                    ),
                    data,
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
        # Commit leases before writing bytes so cleanup cannot race these publications.
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
                    # The write may have finished after cleanup deleted the same key.
                    # Track it again so cleanup sees the newly written object.
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
            MediaArtifactRole.VIDEO_THUMBNAIL: "posters",
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
            msg = tr(t"media-media-uploads-staged = Media uploads must be staged as regular files.")
            raise ValidationError(msg)
        if initial.st_size <= 0:
            msg = tr(t"media-media-uploads-empty = Media uploads cannot be empty.")
            raise ValidationError(msg)
        if initial.st_size > max_bytes:
            limit = max_bytes
            raise ValidationError(
                md_tr(t"media-media-upload-exceeds = Media upload exceeds the {limit}-byte source limit.")
            )
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
            msg = tr(t"media-media-upload-changed = Media upload changed while it was being staged.")
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
        msg = tr(t"media-media-sha-lowercase = Media SHA-256 values must be lowercase hexadecimal.")
        raise ValidationError(msg)


def _require_object_key(value: str) -> None:
    normalized = PurePosixPath(value)
    if normalized.is_absolute() or not normalized.parts or any(part in {"", ".", ".."} for part in normalized.parts):
        msg = tr(t"media-media-object-keys = Media object keys must be non-empty relative paths without traversal.")
        raise ValidationError(msg)


def _require_content_type(value: str) -> None:
    if value != value.strip() or not value or len(value) > 255 or any(ord(character) < 32 for character in value):
        msg = tr(
            t"media-media-content-types = Media content "
            t"types must be 1-255 printable characters without surrounding whitespace."
        )
        raise ValidationError(msg)


class MediaUnitOfWork(UnitOfWork, Protocol):
    @property
    def media_jobs(self) -> MediaJobRepository: ...
