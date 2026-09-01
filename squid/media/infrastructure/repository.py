"""PostgreSQL metadata and claim-token queue for media normalization."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast, override
from uuid import UUID, uuid4

from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy import delete as sql_delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.media.application.jobs import (
    MEDIA_ARTIFACT_CLEANUP_CLAIM,
    MEDIA_ARTIFACT_PUBLICATION_LEASE,
    ClaimedMediaJob,
    MediaArtifactCleanupInProgressError,
    MediaArtifactCleanupOutcome,
    MediaArtifactRole,
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
from squid.media.errors import MediaDraftNotFoundError, MediaDraftStateConflictError, MediaLimitExceededError
from squid.media.infrastructure.models import (
    MediaArtifactObjectRecord,
    MediaArtifactPublicationRecord,
    MediaArtifactRecord,
    MediaNormalizationJobRecord,
    MediaUploadRecord,
)
from squid.persistence.advisory_locks import SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE, lock_uuid
from squid.persistence.queue import VISIBILITY_TIMEOUT, retry_delay
from squid.submissions.domain import DraftStatus
from squid.submissions.infrastructure.models import SubmissionDraft

_MEDIA_UPLOAD_LOCK_NAMESPACE = "media-upload-registration-v1"


@dataclass(frozen=True, slots=True)
class _ClaimedArtifactCleanup:
    object_key: str
    claim_token: UUID


class PostgresMediaJobRepository(MediaJobRepository):
    """Persist immutable uploads and fence every worker transition on a UUID token."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def enqueue(self, upload: MediaUploadMetadata, limits: MediaLimits) -> MediaEnqueueOutcome:
        """Reserve aggregate draft capacity, or accept an identical retry."""
        async with self._session_factory.begin() as session:
            await lock_uuid(session, upload.id, namespace=_MEDIA_UPLOAD_LOCK_NAMESPACE)
            existing = await session.get(MediaUploadRecord, upload.id)
            if existing is not None:
                existing_job = await session.get(MediaNormalizationJobRecord, upload.id)
                if existing_job is None:
                    msg = "Persisted media upload is missing its normalization job."
                    raise RuntimeError(msg)
                existing_status = MediaJobStatus(existing_job.status)
                if not _same_upload(existing, upload):
                    raise MediaUploadConflictError(
                        upload.id,
                        existing_source_object_key=existing.source_object_key,
                        existing_status=existing_status,
                    )
                return MediaEnqueueOutcome(created=False, status=existing_status)

            await _lock_mutable_submission_draft(session, upload.draft_id)
            totals = await _active_totals(session, upload.draft_id)
            candidate = MediaBatchTotals(
                image_count=totals.image_count + int(upload.kind is MediaKind.IMAGE),
                video_count=totals.video_count + int(upload.kind is MediaKind.VIDEO),
                source_bytes=totals.source_bytes + upload.source_byte_size,
                output_bytes=totals.output_bytes,
            )
            if violation := limits.batch_violation(candidate):
                raise MediaLimitExceededError(violation)
            session.add(
                MediaUploadRecord(
                    id=upload.id,
                    draft_id=upload.draft_id,
                    kind=upload.kind.value,
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
            if job.status == MediaJobStatus.DISCARDED.value:
                return True
            job.status = MediaJobStatus.DISCARDED.value
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
                MediaNormalizationJobRecord.status == MediaJobStatus.PENDING.value,
                MediaNormalizationJobRecord.available_at <= func.now(),
            ),
            and_(
                MediaNormalizationJobRecord.status == MediaJobStatus.CLAIMED.value,
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
                job.status = MediaJobStatus.CLAIMED.value
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
            await session.execute(
                update(MediaArtifactPublicationRecord)
                .where(
                    MediaArtifactPublicationRecord.upload_id == job.upload.id,
                    MediaArtifactPublicationRecord.claim_token == job.claim_token,
                )
                .values(expires_at=_publication_expiry(), renewed_at=func.now())
            )
        return True

    @override
    async def defer(self, job: ClaimedMediaJob, *, until: Instant) -> bool:
        """Release a cleanup-blocked claim without charging a failed attempt."""
        statement = (
            update(MediaNormalizationJobRecord)
            .where(*_claim_filter(job))
            .values(
                status=MediaJobStatus.PENDING.value,
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
                await session.execute(
                    sql_delete(MediaArtifactPublicationRecord).where(
                        MediaArtifactPublicationRecord.upload_id == job.upload.id,
                        MediaArtifactPublicationRecord.claim_token == job.claim_token,
                    )
                )
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
                    MediaNormalizationJobRecord.status == MediaJobStatus.COMPLETED.value,
                    MediaArtifactRecord.role.in_((MediaArtifactRole.OUTPUT.value, MediaArtifactRole.POSTER.value)),
                )
            )
            proposed_output_bytes = sum(
                artifact.byte_size
                for artifact in artifacts
                if artifact.role in {MediaArtifactRole.OUTPUT, MediaArtifactRole.POSTER}
            )
            totals = MediaBatchTotals(output_bytes=int(existing_output_bytes or 0) + proposed_output_bytes)
            if violation := limits.batch_violation(totals):
                raise MediaLimitExceededError(violation)
            statement = (
                update(MediaNormalizationJobRecord)
                .where(*_claim_filter(job))
                .values(
                    status=MediaJobStatus.COMPLETED.value,
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
                    role=artifact.role.value,
                    object_key=artifact.object_key,
                    content_type=artifact.content_type,
                    byte_size=artifact.byte_size,
                    sha256=artifact.sha256,
                    width=artifact.width,
                    height=artifact.height,
                )
                for artifact in artifacts
            )
            await _release_artifact_leases(session, job, artifacts)
        return True

    @override
    async def track_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> bool:
        """Register possible object keys before upload and report whether the claim is current."""
        _validate_completed_artifacts(job.upload.kind, artifacts)
        async with self._session_factory.begin() as session:
            current_claim = await session.scalar(
                select(MediaNormalizationJobRecord.upload_id).where(*_claim_filter(job)).with_for_update()
            )
            object_keys = tuple(sorted(artifact.object_key for artifact in artifacts))
            existing_objects = tuple(
                (
                    await session.scalars(
                        select(MediaArtifactObjectRecord)
                        .where(MediaArtifactObjectRecord.object_key.in_(object_keys))
                        .order_by(MediaArtifactObjectRecord.object_key)
                        .with_for_update()
                    )
                ).all()
            )
            cleanup_claims = tuple(
                row.cleanup_claimed_at for row in existing_objects if row.cleanup_claim_token is not None
            )
            if current_claim is not None and cleanup_claims:
                retry_at = max(
                    claimed_at.add(seconds=int(MEDIA_ARTIFACT_CLEANUP_CLAIM.total_seconds()))
                    for claimed_at in cleanup_claims
                    if claimed_at is not None
                )
                raise MediaArtifactCleanupInProgressError(retry_at)
            for artifact in artifacts:
                statement = insert(MediaArtifactObjectRecord).values(
                    object_key=artifact.object_key,
                    sha256=artifact.sha256,
                    byte_size=artifact.byte_size,
                    first_upload_id=job.upload.id,
                    last_upload_id=job.upload.id,
                )
                was_deleted = MediaArtifactObjectRecord.deleted_at.is_not(None)
                statement = statement.on_conflict_do_update(
                    index_elements=[MediaArtifactObjectRecord.object_key],
                    set_={
                        "last_upload_id": job.upload.id,
                        "last_seen_at": func.now(),
                        "available_at": case(
                            (was_deleted, func.now()),
                            else_=MediaArtifactObjectRecord.available_at,
                        ),
                        "attempts": case((was_deleted, 0), else_=MediaArtifactObjectRecord.attempts),
                        "last_error": case((was_deleted, None), else_=MediaArtifactObjectRecord.last_error),
                        "deleted_at": None,
                    },
                    where=and_(
                        MediaArtifactObjectRecord.sha256 == statement.excluded.sha256,
                        MediaArtifactObjectRecord.byte_size == statement.excluded.byte_size,
                    ),
                )
                outcome = cast(CursorResult[Any], await session.execute(statement))
                if not outcome.rowcount:
                    msg = f"Media artifact object metadata conflicts for {artifact.object_key}."
                    raise ValueError(msg)
                if current_claim is not None:
                    lease = insert(MediaArtifactPublicationRecord).values(
                        object_key=artifact.object_key,
                        upload_id=job.upload.id,
                        claim_token=job.claim_token,
                        expires_at=_publication_expiry(),
                    )
                    lease = lease.on_conflict_do_update(
                        index_elements=[
                            MediaArtifactPublicationRecord.object_key,
                            MediaArtifactPublicationRecord.upload_id,
                            MediaArtifactPublicationRecord.claim_token,
                        ],
                        set_={
                            "expires_at": lease.excluded.expires_at,
                            "renewed_at": func.now(),
                        },
                    )
                    await session.execute(lease)
        return current_claim is not None

    @override
    async def release_artifacts(
        self,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None:
        """Release exactly one claim's leases after its object-store writes have stopped."""
        async with self._session_factory.begin() as session:
            await _release_artifact_leases(session, job, artifacts)

    @override
    async def cleanup_artifacts(
        self,
        delete: Callable[[str], Awaitable[None]],
        *,
        limit: int,
    ) -> MediaArtifactCleanupOutcome:
        """Claim due keys, perform storage I/O outside transactions, and token-fence acknowledgments."""
        async with self._session_factory.begin() as session:
            await _revoke_expired_publications(session, limit=limit)
            await _reconcile_artifact_objects(session)
            live_reference = exists(
                select(MediaArtifactRecord.id)
                .join(
                    MediaNormalizationJobRecord,
                    MediaNormalizationJobRecord.upload_id == MediaArtifactRecord.upload_id,
                )
                .where(
                    MediaArtifactRecord.object_key == MediaArtifactObjectRecord.object_key,
                    MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED.value,
                )
                .correlate(MediaArtifactObjectRecord)
            )
            publication_pending = exists(
                select(MediaArtifactPublicationRecord.object_key)
                .where(
                    MediaArtifactPublicationRecord.object_key == MediaArtifactObjectRecord.object_key,
                )
                .correlate(MediaArtifactObjectRecord)
            )
            candidates = tuple(
                (
                    await session.scalars(
                        select(MediaArtifactObjectRecord)
                        .where(
                            MediaArtifactObjectRecord.deleted_at.is_(None),
                            MediaArtifactObjectRecord.available_at <= func.now(),
                            or_(
                                MediaArtifactObjectRecord.cleanup_claimed_at.is_(None),
                                MediaArtifactObjectRecord.cleanup_claimed_at
                                < func.now() - MEDIA_ARTIFACT_CLEANUP_CLAIM,
                            ),
                            ~live_reference,
                            ~publication_pending,
                        )
                        .order_by(MediaArtifactObjectRecord.available_at, MediaArtifactObjectRecord.object_key)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for candidate in candidates:
                candidate.cleanup_claimed_at = Instant.now()
                candidate.cleanup_claim_token = uuid4()
            claims = tuple(
                _ClaimedArtifactCleanup(candidate.object_key, cast(UUID, candidate.cleanup_claim_token))
                for candidate in candidates
            )

        deleted = 0
        failed = 0
        resolutions: list[tuple[_ClaimedArtifactCleanup, str | None]] = []
        for claim in claims:
            error_name: str | None = None
            try:
                await delete(claim.object_key)
            except Exception as error:
                failed += 1
                error_name = type(error).__name__[:4000]
            else:
                deleted += 1
            resolutions.append((claim, error_name))
        await self._resolve_artifact_cleanups(resolutions)
        return MediaArtifactCleanupOutcome(len(claims), deleted, failed)

    async def _resolve_artifact_cleanups(
        self,
        resolutions: Sequence[tuple[_ClaimedArtifactCleanup, str | None]],
    ) -> None:
        """Acknowledge a whole cleanup batch in one transaction.

        Deletion is idempotent and an unacknowledged claim expires on its own,
        so a crash here costs a repeated delete rather than a lost object --
        which is all a transaction per object was buying.
        """
        if not resolutions:
            return
        tokens = {claim.object_key: claim.claim_token for claim, _ in resolutions}
        errors = {claim.object_key: error_name for claim, error_name in resolutions}
        async with self._session_factory.begin() as session:
            candidates = (
                await session.scalars(
                    select(MediaArtifactObjectRecord)
                    .where(
                        MediaArtifactObjectRecord.object_key.in_(tokens),
                        MediaArtifactObjectRecord.cleanup_claim_token.in_(tokens.values()),
                    )
                    .order_by(MediaArtifactObjectRecord.object_key)
                    .with_for_update()
                )
            ).all()
            for candidate in candidates:
                # Both halves of the pair have to match, exactly as the
                # per-object statement required: one row's key with another
                # row's token acknowledges a claim nobody holds.
                if tokens.get(candidate.object_key) != candidate.cleanup_claim_token:
                    continue
                error_name = errors[candidate.object_key]
                cleanup_started_at = candidate.cleanup_claimed_at
                candidate.cleanup_claimed_at = None
                candidate.cleanup_claim_token = None
                if error_name is not None:
                    candidate.attempts += 1
                    candidate.available_at = Instant.now().add(
                        seconds=int(retry_delay(candidate.attempts).total_seconds())
                    )
                    candidate.last_error = error_name
                elif cleanup_started_at is not None and candidate.last_seen_at > cleanup_started_at:
                    candidate.available_at = Instant.now()
                    candidate.last_error = None
                else:
                    candidate.deleted_at = Instant.now()
                    candidate.last_error = None

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
                status=MediaJobStatus.DEAD.value,
                completed_at=None,
                dead_at=func.now(),
                discarded_at=None,
            )
        else:
            values.update(
                status=MediaJobStatus.PENDING.value,
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
                                MediaJobStatus.COMPLETED.value,
                                MediaJobStatus.DEAD.value,
                                MediaJobStatus.DISCARDED.value,
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
                        MediaJobStatus.COMPLETED.value,
                        MediaJobStatus.DEAD.value,
                        MediaJobStatus.DISCARDED.value,
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


async def _lock_mutable_submission_draft(session: AsyncSession, draft_id: UUID) -> None:
    await lock_uuid(session, draft_id, namespace=SUBMISSION_DRAFT_LIFECYCLE_LOCK_NAMESPACE)
    status = await session.scalar(
        select(SubmissionDraft.status).where(SubmissionDraft.id == draft_id).with_for_update()
    )
    if status is None:
        # Once the draft row is gone there is no owner against which a public mutation can be authorized.
        raise MediaDraftNotFoundError(draft_id)
    if DraftStatus(status) not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
        raise MediaDraftStateConflictError(status)


def _publication_expiry() -> Instant:
    return Instant.now().add(seconds=int(MEDIA_ARTIFACT_PUBLICATION_LEASE.total_seconds()))


async def _reconcile_artifact_objects(session: AsyncSession) -> None:
    """Discover artifact rows committed by workers predating lifecycle tracking."""
    lifecycle_missing_or_stale = or_(
        MediaArtifactObjectRecord.object_key.is_(None),
        and_(
            MediaArtifactObjectRecord.deleted_at.is_not(None),
            MediaArtifactRecord.created_at > MediaArtifactObjectRecord.deleted_at,
        ),
    )
    source = (
        select(
            MediaArtifactRecord.object_key,
            MediaArtifactRecord.sha256,
            MediaArtifactRecord.byte_size,
            MediaArtifactRecord.upload_id,
            MediaArtifactRecord.upload_id,
            func.now() + MEDIA_ARTIFACT_PUBLICATION_LEASE,
            MediaArtifactRecord.created_at,
            MediaArtifactRecord.created_at,
        )
        .outerjoin(
            MediaArtifactObjectRecord,
            MediaArtifactObjectRecord.object_key == MediaArtifactRecord.object_key,
        )
        .where(lifecycle_missing_or_stale)
        .distinct(MediaArtifactRecord.object_key)
        .order_by(MediaArtifactRecord.object_key, MediaArtifactRecord.created_at.desc(), MediaArtifactRecord.id.desc())
    )
    statement = insert(MediaArtifactObjectRecord).from_select(
        (
            "object_key",
            "sha256",
            "byte_size",
            "first_upload_id",
            "last_upload_id",
            "available_at",
            "first_seen_at",
            "last_seen_at",
        ),
        source,
    )
    newer_than_tombstone = and_(
        MediaArtifactObjectRecord.deleted_at.is_not(None),
        statement.excluded.last_seen_at > MediaArtifactObjectRecord.deleted_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[MediaArtifactObjectRecord.object_key],
        set_={
            "last_upload_id": statement.excluded.last_upload_id,
            "last_seen_at": func.greatest(
                MediaArtifactObjectRecord.last_seen_at,
                statement.excluded.last_seen_at,
            ),
            "available_at": case(
                (newer_than_tombstone, statement.excluded.available_at),
                else_=MediaArtifactObjectRecord.available_at,
            ),
            "attempts": case((newer_than_tombstone, 0), else_=MediaArtifactObjectRecord.attempts),
            "last_error": case((newer_than_tombstone, None), else_=MediaArtifactObjectRecord.last_error),
            "deleted_at": case((newer_than_tombstone, None), else_=MediaArtifactObjectRecord.deleted_at),
        },
        where=and_(
            MediaArtifactObjectRecord.sha256 == statement.excluded.sha256,
            MediaArtifactObjectRecord.byte_size == statement.excluded.byte_size,
            newer_than_tombstone,
        ),
    )
    await session.execute(statement)


async def _revoke_expired_publications(session: AsyncSession, *, limit: int) -> None:
    """Fence an expired publisher before releasing its objects for deletion."""
    identities = tuple(
        (
            await session.execute(
                select(
                    MediaArtifactPublicationRecord.upload_id,
                    MediaArtifactPublicationRecord.claim_token,
                )
                .where(MediaArtifactPublicationRecord.expires_at <= func.now())
                .distinct()
                .order_by(
                    MediaArtifactPublicationRecord.upload_id,
                    MediaArtifactPublicationRecord.claim_token,
                )
                .limit(limit)
            )
        ).all()
    )
    for upload_id, claim_token in identities:
        job = await session.scalar(
            select(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == upload_id)
            .with_for_update()
        )
        expired = await session.scalar(
            select(MediaArtifactPublicationRecord.object_key)
            .where(
                MediaArtifactPublicationRecord.upload_id == upload_id,
                MediaArtifactPublicationRecord.claim_token == claim_token,
                MediaArtifactPublicationRecord.expires_at <= func.now(),
            )
            .limit(1)
            .with_for_update()
        )
        if expired is None:
            continue
        if job is not None and job.status == MediaJobStatus.CLAIMED.value and job.claim_token == claim_token:
            job.status = MediaJobStatus.PENDING.value
            job.available_at = Instant.now()
            job.claimed_at = None
            job.claim_token = None
            job.completed_at = None
            job.dead_at = None
            job.discarded_at = None
            job.last_error = "publication_lease_expired"
        await session.execute(
            sql_delete(MediaArtifactPublicationRecord).where(
                MediaArtifactPublicationRecord.upload_id == upload_id,
                MediaArtifactPublicationRecord.claim_token == claim_token,
            )
        )


async def _release_artifact_leases(
    session: AsyncSession,
    job: ClaimedMediaJob,
    artifacts: Sequence[StoredMediaArtifact],
) -> None:
    object_keys = tuple(artifact.object_key for artifact in artifacts)
    if not object_keys:
        return
    await session.execute(
        sql_delete(MediaArtifactPublicationRecord).where(
            MediaArtifactPublicationRecord.object_key.in_(object_keys),
            MediaArtifactPublicationRecord.upload_id == job.upload.id,
            MediaArtifactPublicationRecord.claim_token == job.claim_token,
        )
    )


async def _active_totals(session: AsyncSession, draft_id: UUID) -> MediaBatchTotals:
    active = (
        MediaJobStatus.PENDING.value,
        MediaJobStatus.CLAIMED.value,
        MediaJobStatus.COMPLETED.value,
    )
    row = (
        await session.execute(
            select(
                func.count(MediaUploadRecord.id).filter(MediaUploadRecord.kind == MediaKind.IMAGE.value),
                func.count(MediaUploadRecord.id).filter(MediaUploadRecord.kind == MediaKind.VIDEO.value),
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
        MediaNormalizationJobRecord.status == MediaJobStatus.CLAIMED.value,
        MediaNormalizationJobRecord.claim_token == job.claim_token,
    )


def _same_upload(record: MediaUploadRecord, upload: MediaUploadMetadata) -> bool:
    return (
        record.draft_id == upload.draft_id
        and record.kind == upload.kind.value
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
        kind=MediaKind(record.kind),
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
        role=MediaArtifactRole(record.role),
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
        status=MediaJobStatus(job.status),
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
    if kind is MediaKind.VIDEO:
        expected.add(MediaArtifactRole.POSTER)
    if len(roles) != len(set(roles)) or set(roles) != expected:
        msg = f"Completed {kind.value} media requires exactly these artifacts: {sorted(expected)}."
        raise ValueError(msg)
