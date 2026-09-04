"""PostgreSQL metadata and claim-token queue for media normalization."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast, override
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.media.application.jobs import (
    MEDIA_VIDEO_THUMBNAIL_ROLES,
    ClaimedMediaJob,
    MediaArtifactCleanupOutcome,
    MediaArtifactRole,
    MediaDraftUploadAuthorization,
    MediaEnqueueOutcome,
    MediaJobFailureOutcome,
    MediaJobRepository,
    MediaJobSnapshot,
    MediaJobStatus,
    MediaUploadConflictError,
    MediaUploadMetadata,
    StoredMediaArtifact,
    TerminalMediaSource,
)
from squid.media.domain import MediaBatchTotals, MediaKind, MediaLimits
from squid.media.errors import (
    MediaDraftNotFoundError,
    MediaDraftRevisionConflictError,
    MediaDraftStateConflictError,
    MediaLimitExceededError,
)
from squid.media.infrastructure.artifact_publication import PostgresArtifactPublicationRepository
from squid.media.infrastructure.models import MediaArtifactRecord, MediaNormalizationJobRecord, MediaUploadRecord
from squid.persistence.advisory_locks import (
    SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE,
    AdvisoryLockNamespace,
    lock_uuid,
)
from squid.persistence.queue import VISIBILITY_TIMEOUT, retry_delay
from squid.submissions.domain import DraftStatus
from squid.submissions.infrastructure.models import SubmissionDraft

_MEDIA_UPLOAD_LOCK_NAMESPACE = AdvisoryLockNamespace.MEDIA_UPLOAD_REGISTRATION


class PostgresMediaJobRepository(MediaJobRepository):
    """Persist immutable uploads and fence every worker transition on a UUID token."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._publications = PostgresArtifactPublicationRepository(session_factory)

    @override
    async def enqueue(
        self,
        upload: MediaUploadMetadata,
        limits: MediaLimits,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> MediaEnqueueOutcome:
        """Reserve aggregate draft capacity, or accept an identical retry."""
        async with self._session_factory.begin() as session:
            await lock_uuid(session, upload.id, namespace=_MEDIA_UPLOAD_LOCK_NAMESPACE)
            await _lock_mutable_submission_draft(session, upload.draft_id, authorization=authorization)
            existing = await session.get(MediaUploadRecord, upload.id)
            if existing is not None:
                existing_job = await session.get(MediaNormalizationJobRecord, upload.id)
                if existing_job is None:
                    msg = "Persisted media upload is missing its normalization job."
                    raise RuntimeError(msg)
                existing_status = existing_job.status
                if not _same_upload(existing, upload):
                    raise MediaUploadConflictError(
                        upload.id,
                        existing_source_object_key=existing.source_object_key,
                        existing_status=existing_status,
                    )
                return MediaEnqueueOutcome(created=False, status=existing_status)

            totals = await _active_totals(session, upload.draft_id)
            candidate = MediaBatchTotals(
                image_count=totals.image_count + int(upload.kind is MediaKind.IMAGE),
                video_count=totals.video_count + int(upload.kind is MediaKind.VIDEO),
                source_bytes=totals.source_bytes + upload.source_byte_size,
                output_bytes=totals.output_bytes,
            )
            if violations := limits.batch_violations(candidate):
                raise MediaLimitExceededError(violations)
            session.add(
                MediaUploadRecord(
                    id=upload.id,
                    draft_id=upload.draft_id,
                    kind=upload.kind,
                    source_content_type=upload.source_content_type,
                    source_byte_size=upload.source_byte_size,
                    source_sha256=upload.source_sha256,
                    source_object_key=upload.source_object_key,
                    strip_audio=upload.strip_audio,
                )
            )
            await session.flush()
            session.add(MediaNormalizationJobRecord(upload_id=upload.id))
            return MediaEnqueueOutcome(created=True, status=MediaJobStatus.PENDING)

    @override
    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(MediaNormalizationJobRecord, MediaUploadRecord)
                    .join(MediaUploadRecord, MediaUploadRecord.id == MediaNormalizationJobRecord.upload_id)
                    .where(MediaNormalizationJobRecord.upload_id == upload_id)
                )
            ).one_or_none()
            if row is None:
                return None
            artifacts = tuple(
                (
                    await session.scalars(
                        select(MediaArtifactRecord)
                        .where(MediaArtifactRecord.upload_id == upload_id)
                        .order_by(MediaArtifactRecord.role)
                    )
                ).all()
            )
        return _snapshot(row[0], row[1], artifacts)

    @override
    async def list_for_draft(self, draft_id: UUID) -> Sequence[MediaJobSnapshot]:
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(MediaNormalizationJobRecord, MediaUploadRecord)
                        .join(MediaUploadRecord, MediaUploadRecord.id == MediaNormalizationJobRecord.upload_id)
                        .where(MediaUploadRecord.draft_id == draft_id)
                        .order_by(MediaUploadRecord.created_at, MediaUploadRecord.id)
                    )
                ).all()
            )
            upload_ids = [upload.id for _, upload in rows]
            artifacts = (
                tuple(
                    (
                        await session.scalars(
                            select(MediaArtifactRecord)
                            .where(MediaArtifactRecord.upload_id.in_(upload_ids))
                            .order_by(MediaArtifactRecord.upload_id, MediaArtifactRecord.role)
                        )
                    ).all()
                )
                if upload_ids
                else ()
            )
        by_upload: dict[UUID, list[MediaArtifactRecord]] = {}
        for artifact in artifacts:
            by_upload.setdefault(artifact.upload_id, []).append(artifact)
        return tuple(_snapshot(job, upload, by_upload.get(upload.id, ())) for job, upload in rows)

    @override
    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        """Fence in-flight work and retain a terminal cleanup record."""
        async with self._session_factory.begin() as session:
            await _lock_mutable_submission_draft(session, draft_id)
            row = (
                await session.execute(
                    select(MediaNormalizationJobRecord, MediaUploadRecord)
                    .join(MediaUploadRecord, MediaUploadRecord.id == MediaNormalizationJobRecord.upload_id)
                    .where(MediaNormalizationJobRecord.upload_id == upload_id)
                    .with_for_update(of=MediaNormalizationJobRecord)
                )
            ).one_or_none()
            if row is None or row[1].draft_id != draft_id:
                return False
            job = row[0]
            if job.status is MediaJobStatus.DISCARDED:
                return True
            job.status = MediaJobStatus.DISCARDED
            job.claimed_at = None
            job.claim_token = None
            job.completed_at = None
            job.dead_at = None
            job.discarded_at = Instant.now()
            job.last_error = None
        return True

    @override
    async def claim(self, *, limit: int) -> Sequence[ClaimedMediaJob]:
        """Lease ready or abandoned work with a fresh token per row."""
        ready = or_(
            and_(
                MediaNormalizationJobRecord.status == MediaJobStatus.PENDING,
                MediaNormalizationJobRecord.available_at <= func.now(),
            ),
            and_(
                MediaNormalizationJobRecord.status == MediaJobStatus.CLAIMED,
                MediaNormalizationJobRecord.claimed_at < func.now() - VISIBILITY_TIMEOUT,
            ),
        )
        claimed_at = Instant.now()
        async with self._session_factory() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(MediaNormalizationJobRecord, MediaUploadRecord)
                        .join(MediaUploadRecord, MediaUploadRecord.id == MediaNormalizationJobRecord.upload_id)
                        .where(ready)
                        .order_by(MediaNormalizationJobRecord.available_at, MediaNormalizationJobRecord.upload_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True, of=MediaNormalizationJobRecord)
                    )
                ).all()
            )
            claims: list[ClaimedMediaJob] = []
            for job, upload in rows:
                claim_token = uuid4()
                job.status = MediaJobStatus.CLAIMED
                job.claimed_at = claimed_at
                job.claim_token = claim_token
                claims.append(
                    ClaimedMediaJob(
                        upload=_upload_metadata(upload),
                        attempts=job.attempts,
                        claimed_at=claimed_at,
                        claim_token=claim_token,
                    )
                )
            await session.commit()
        return tuple(claims)

    @override
    async def heartbeat(self, job: ClaimedMediaJob) -> bool:
        """Renew a current job claim and its crash-recovery publication leases."""
        async with self._session_factory.begin() as session:
            current = await session.scalar(
                select(MediaNormalizationJobRecord).where(*_claim_filter(job)).with_for_update()
            )
            if current is None:
                return False
            current.claimed_at = Instant.now()
            await self._publications.renew_in_transaction(session, job)
        return True

    @override
    async def defer(self, job: ClaimedMediaJob, *, until: Instant) -> bool:
        """Release a cleanup-blocked claim without charging a failed attempt."""
        statement = (
            update(MediaNormalizationJobRecord)
            .where(*_claim_filter(job))
            .values(
                status=MediaJobStatus.PENDING,
                available_at=until,
                claimed_at=None,
                claim_token=None,
                completed_at=None,
                dead_at=None,
                discarded_at=None,
                last_error="artifact_cleanup_in_progress",
            )
        )
        async with self._session_factory.begin() as session:
            outcome = cast(CursorResult[Any], await session.execute(statement))
            if outcome.rowcount:
                await self._publications.release_claim_in_transaction(session, job)
        return bool(outcome.rowcount)

    @override
    async def complete(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
        limits: MediaLimits,
    ) -> bool:
        """Atomically persist outputs and complete the job if the claim is current."""
        _validate_completed_artifacts(job.upload.kind, artifacts)
        if not await self.track_artifacts(job, artifacts):
            return False
        async with self._session_factory.begin() as session:
            await lock_uuid(
                session,
                job.upload.draft_id,
                namespace=SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE,
            )
            existing_output_bytes = await session.scalar(
                select(func.coalesce(func.sum(MediaArtifactRecord.byte_size), 0))
                .join(MediaUploadRecord, MediaUploadRecord.id == MediaArtifactRecord.upload_id)
                .join(
                    MediaNormalizationJobRecord,
                    MediaNormalizationJobRecord.upload_id == MediaUploadRecord.id,
                )
                .where(
                    MediaUploadRecord.draft_id == job.upload.draft_id,
                    MediaNormalizationJobRecord.status == MediaJobStatus.COMPLETED,
                    MediaArtifactRecord.role.in_(
                        (
                            MediaArtifactRole.OUTPUT,
                            MediaArtifactRole.POSTER,
                            MediaArtifactRole.VIDEO_THUMBNAIL,
                        )
                    ),
                )
            )
            proposed_output_bytes = sum(
                artifact.byte_size
                for artifact in artifacts
                if artifact.role is MediaArtifactRole.OUTPUT or artifact.role in MEDIA_VIDEO_THUMBNAIL_ROLES
            )
            totals = MediaBatchTotals(output_bytes=int(existing_output_bytes or 0) + proposed_output_bytes)
            if violations := limits.batch_violations(totals):
                raise MediaLimitExceededError(violations)
            statement = (
                update(MediaNormalizationJobRecord)
                .where(*_claim_filter(job))
                .values(
                    status=MediaJobStatus.COMPLETED,
                    claimed_at=None,
                    claim_token=None,
                    completed_at=func.now(),
                    dead_at=None,
                    discarded_at=None,
                    last_error=None,
                )
                .returning(MediaNormalizationJobRecord.upload_id)
            )
            applied = await session.scalar(statement)
            if applied is None:
                return False
            session.add_all(
                MediaArtifactRecord(
                    upload_id=job.upload.id,
                    role=artifact.role,
                    object_key=artifact.object_key,
                    content_type=artifact.content_type,
                    byte_size=artifact.byte_size,
                    sha256=artifact.sha256,
                    width=artifact.width,
                    height=artifact.height,
                )
                for artifact in artifacts
            )
            await self._publications.release_in_transaction(session, job, artifacts)
        return True

    @override
    async def track_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> bool:
        """Register possible object keys before upload and report whether the claim is current."""
        _validate_completed_artifacts(job.upload.kind, artifacts)
        return await self._publications.track(job, artifacts)

    @override
    async def release_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None:
        """Release exactly one claim's leases after its object-store writes have stopped."""
        await self._publications.release(job, artifacts)

    @override
    async def cleanup_artifacts(
        self,
        delete: Callable[[str], Awaitable[None]],
        *,
        limit: int,
    ) -> MediaArtifactCleanupOutcome:
        """Claim due keys, perform storage I/O outside transactions, and token-fence acknowledgments."""
        return await self._publications.cleanup(delete, limit=limit)


    @override
    async def fail(
        self,
        job: ClaimedMediaJob,
        error: str,
        *,
        max_attempts: int,
        terminal: bool,
    ) -> MediaJobFailureOutcome:
        """Release retryable work with backoff or retain a dead terminal row."""
        attempts = job.attempts + 1
        dead = terminal or attempts >= max_attempts
        values: dict[str, object] = {
            "attempts": attempts,
            "claimed_at": None,
            "claim_token": None,
            "last_error": error[:4000],
        }
        if dead:
            values.update(
                status=MediaJobStatus.DEAD,
                completed_at=None,
                dead_at=func.now(),
                discarded_at=None,
            )
        else:
            values.update(
                status=MediaJobStatus.PENDING,
                available_at=func.now() + retry_delay(attempts),
                completed_at=None,
                dead_at=None,
                discarded_at=None,
            )
        statement = update(MediaNormalizationJobRecord).where(*_claim_filter(job)).values(**values)
        async with self._session_factory.begin() as session:
            outcome = cast(CursorResult[Any], await session.execute(statement))
        applied = bool(outcome.rowcount)
        return MediaJobFailureOutcome(applied=applied, dead=dead and applied)

    @override
    async def terminal_sources(self, *, limit: int) -> Sequence[TerminalMediaSource]:
        """List raw objects whose terminal metadata still lacks cleanup confirmation."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MediaUploadRecord.id, MediaUploadRecord.source_object_key)
                    .join(
                        MediaNormalizationJobRecord,
                        MediaNormalizationJobRecord.upload_id == MediaUploadRecord.id,
                    )
                    .where(
                        MediaUploadRecord.raw_deleted_at.is_(None),
                        MediaNormalizationJobRecord.status.in_(
                            (
                                MediaJobStatus.COMPLETED,
                                MediaJobStatus.DEAD,
                                MediaJobStatus.DISCARDED,
                            )
                        ),
                    )
                    .order_by(MediaUploadRecord.created_at, MediaUploadRecord.id)
                    .limit(limit)
                )
            ).all()
        return tuple(TerminalMediaSource(upload_id, object_key) for upload_id, object_key in rows)

    @override
    async def mark_source_deleted(self, source: TerminalMediaSource) -> bool:
        """Confirm an idempotent raw-object deletion only while the job is terminal."""
        terminal_job = exists(
            select(MediaNormalizationJobRecord.upload_id).where(
                MediaNormalizationJobRecord.upload_id == source.upload_id,
                MediaNormalizationJobRecord.status.in_(
                    (
                        MediaJobStatus.COMPLETED,
                        MediaJobStatus.DEAD,
                        MediaJobStatus.DISCARDED,
                    )
                ),
            )
        )
        statement = (
            update(MediaUploadRecord)
            .where(
                MediaUploadRecord.id == source.upload_id,
                MediaUploadRecord.source_object_key == source.object_key,
                MediaUploadRecord.raw_deleted_at.is_(None),
                terminal_job,
            )
            .values(raw_deleted_at=func.now())
        )
        async with self._session_factory.begin() as session:
            outcome = cast(CursorResult[Any], await session.execute(statement))
        return bool(outcome.rowcount)


async def _lock_mutable_submission_draft(
    session: AsyncSession,
    draft_id: UUID,
    *,
    authorization: MediaDraftUploadAuthorization | None = None,
) -> None:
    await lock_uuid(session, draft_id, namespace=SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE)
    row = (
        await session.execute(
            select(SubmissionDraft.status, SubmissionDraft.owner_account_id, SubmissionDraft.revision)
            .where(SubmissionDraft.id == draft_id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        # Once the draft row is gone there is no owner against which a public mutation can be authorized.
        raise MediaDraftNotFoundError(draft_id)
    status, owner_account_id, revision = row
    if authorization is not None:
        if owner_account_id != authorization.owner_account_id:
            raise MediaDraftNotFoundError(draft_id)
        if revision != authorization.draft_revision:
            raise MediaDraftRevisionConflictError(expected=authorization.draft_revision, actual=revision)
    if DraftStatus(status) not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
        raise MediaDraftStateConflictError(status)


async def _active_totals(session: AsyncSession, draft_id: UUID) -> MediaBatchTotals:
    active = (
        MediaJobStatus.PENDING,
        MediaJobStatus.CLAIMED,
        MediaJobStatus.COMPLETED,
    )
    row = (
        await session.execute(
            select(
                func.count(MediaUploadRecord.id).filter(MediaUploadRecord.kind == MediaKind.IMAGE),
                func.count(MediaUploadRecord.id).filter(MediaUploadRecord.kind == MediaKind.VIDEO),
                func.coalesce(func.sum(MediaUploadRecord.source_byte_size), 0),
            )
            .join(
                MediaNormalizationJobRecord,
                MediaNormalizationJobRecord.upload_id == MediaUploadRecord.id,
            )
            .where(
                MediaUploadRecord.draft_id == draft_id,
                MediaNormalizationJobRecord.status.in_(active),
            )
        )
    ).one()
    return MediaBatchTotals(
        image_count=int(row[0]),
        video_count=int(row[1]),
        source_bytes=int(row[2]),
    )


def _claim_filter(job: ClaimedMediaJob) -> tuple[Any, ...]:
    return (
        MediaNormalizationJobRecord.upload_id == job.upload.id,
        MediaNormalizationJobRecord.status == MediaJobStatus.CLAIMED,
        MediaNormalizationJobRecord.claim_token == job.claim_token,
    )


def _same_upload(record: MediaUploadRecord, upload: MediaUploadMetadata) -> bool:
    return (
        record.draft_id == upload.draft_id
        and record.kind is upload.kind
        and record.source_content_type == upload.source_content_type
        and record.source_byte_size == upload.source_byte_size
        and record.source_sha256 == upload.source_sha256
        and record.source_object_key == upload.source_object_key
        and record.strip_audio == upload.strip_audio
    )


def _upload_metadata(record: MediaUploadRecord) -> MediaUploadMetadata:
    return MediaUploadMetadata(
        id=record.id,
        draft_id=record.draft_id,
        kind=record.kind,
        source_content_type=record.source_content_type,
        source_byte_size=record.source_byte_size,
        source_sha256=record.source_sha256,
        source_object_key=record.source_object_key,
        strip_audio=record.strip_audio,
        created_at=record.created_at,
        raw_deleted_at=record.raw_deleted_at,
    )


def _stored_artifact(record: MediaArtifactRecord) -> StoredMediaArtifact:
    return StoredMediaArtifact(
        role=record.role,
        object_key=record.object_key,
        content_type=record.content_type,
        byte_size=record.byte_size,
        sha256=record.sha256,
        width=record.width,
        height=record.height,
    )


def _snapshot(
    job: MediaNormalizationJobRecord,
    upload: MediaUploadRecord,
    artifacts: Sequence[MediaArtifactRecord],
) -> MediaJobSnapshot:
    return MediaJobSnapshot(
        upload=_upload_metadata(upload),
        status=job.status,
        attempts=job.attempts,
        available_at=job.available_at,
        claimed_at=job.claimed_at,
        claim_token=job.claim_token,
        completed_at=job.completed_at,
        dead_at=job.dead_at,
        discarded_at=job.discarded_at,
        last_error=job.last_error,
        artifacts=tuple(_stored_artifact(artifact) for artifact in artifacts),
    )


def _validate_completed_artifacts(kind: MediaKind, artifacts: Sequence[StoredMediaArtifact]) -> None:
    roles = [artifact.role for artifact in artifacts]
    expected = {MediaArtifactRole.OUTPUT, MediaArtifactRole.REPORT}
    role_set = set(roles)
    valid_thumbnail = len(role_set & MEDIA_VIDEO_THUMBNAIL_ROLES) == int(kind is MediaKind.VIDEO)
    if (
        len(roles) != len(role_set)
        or role_set - MEDIA_VIDEO_THUMBNAIL_ROLES != expected
        or not valid_thumbnail
    ):
        msg = f"Completed {kind.value} media requires exactly these artifacts: {sorted(expected)}."
        raise ValueError(msg)
