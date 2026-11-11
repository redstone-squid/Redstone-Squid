"""PostgreSQL metadata and claim-token queue for media normalization."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, cast, override
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.edits.infrastructure import retained_resource
from squid.media.application.jobs import (
    MEDIA_VIDEO_THUMBNAIL_ROLES,
    ArtifactCleanupClaim,
    ClaimedMediaJob,
    MediaDraftUploadAuthorization,
    MediaEnqueueOutcome,
    MediaJobFailureOutcome,
    MediaJobRepository,
    MediaJobSnapshot,
    MediaUploadConflictError,
    MediaUploadMetadata,
    StoredMediaArtifact,
    TerminalMediaSource,
)
from squid.media.domain import MediaBatchTotals, MediaKind, MediaLimits
from squid.media.domain.jobs import MediaArtifactRole, MediaJobStatus
from squid.media.errors import (
    MediaDraftNotFoundError,
    MediaDraftRevisionConflictError,
    MediaDraftStateConflictError,
    MediaLimitExceededError,
)
from squid.media.infrastructure.artifact_publication import PostgresArtifactPublicationRepository
from squid.media.infrastructure.references import media_for_draft, retained_elsewhere
from squid.media.models import (
    MediaArtifactRecord,
    MediaDraftReference,
    MediaNormalizationJobRecord,
    MediaUploadRecord,
)
from squid.persistence.advisory_locks import (
    AdvisoryLockNamespace,
    lock_uuid,
)
from squid.persistence.queue import VISIBILITY_TIMEOUT, retry_delay
from squid.persistence.types import database_now
from squid.submissions.domain import DraftStatus
from squid.submissions.models.entities import SubmissionDraft


class PostgresMediaJobRepository(MediaJobRepository):
    """Persist uploads and accept worker results only for the current claim token.

    Methods use the caller's session and do not commit. Draft lifecycle locks
    serialize capacity changes; job tokens prevent an older worker from completing
    or failing work that has since been claimed again.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._publications = PostgresArtifactPublicationRepository(session)

    @override
    async def enqueue(
        self,
        upload: MediaUploadMetadata,
        limits: MediaLimits,
        *,
        authorization: MediaDraftUploadAuthorization | None = None,
    ) -> MediaEnqueueOutcome:
        """Reserve aggregate draft capacity, or accept an identical retry."""
        session = self._session()
        await lock_uuid(session, upload.id, namespace=AdvisoryLockNamespace.MEDIA_UPLOAD_REGISTRATION)
        draft_status = await _lock_mutable_submission_draft(
            session,
            upload.draft_id,
            authorization=authorization,
            require_editable=False,
        )
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

        if draft_status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            raise MediaDraftStateConflictError(draft_status)
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
        session.add(MediaDraftReference(draft_id=upload.draft_id, upload_id=upload.id))
        return MediaEnqueueOutcome(created=True, status=MediaJobStatus.PENDING)

    @override
    async def get(self, upload_id: UUID) -> MediaJobSnapshot | None:
        session = self._session()
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
        session = self._session()
        rows = tuple(
            (
                await session.execute(
                    select(MediaNormalizationJobRecord, MediaUploadRecord)
                    .join(MediaUploadRecord, MediaUploadRecord.id == MediaNormalizationJobRecord.upload_id)
                    .where(media_for_draft(draft_id, include_discarded=True))
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
        discarded = set(
            await session.scalars(
                select(MediaDraftReference.upload_id).where(
                    MediaDraftReference.draft_id == draft_id,
                    MediaDraftReference.discarded.is_(True),
                )
            )
        )
        by_upload: dict[UUID, list[MediaArtifactRecord]] = {}
        for artifact in artifacts:
            by_upload.setdefault(artifact.upload_id, []).append(artifact)
        return tuple(
            replace(
                snapshot,
                upload=replace(snapshot.upload, draft_id=draft_id),
                status=MediaJobStatus.DISCARDED if upload.id in discarded else snapshot.status,
            )
            for job, upload in rows
            for snapshot in (_snapshot(job, upload, by_upload.get(upload.id, ())),)
        )

    @override
    async def discard(self, draft_id: UUID, upload_id: UUID) -> bool:
        """Discard this draft's reference and return whether the upload belongs to it.

        Another retained draft reference keeps the shared job alive. Otherwise,
        discard the job and clear its claim so cleanup can remove its objects.
        """
        session = self._session()
        await _lock_mutable_submission_draft(session, draft_id)
        row = (
            await session.execute(
                select(MediaNormalizationJobRecord, MediaUploadRecord)
                .join(MediaUploadRecord, MediaUploadRecord.id == MediaNormalizationJobRecord.upload_id)
                .where(MediaNormalizationJobRecord.upload_id == upload_id)
                .with_for_update(of=MediaNormalizationJobRecord)
            )
        ).one_or_none()
        if row is None or not await session.scalar(
            select(MediaUploadRecord.id).where(
                MediaUploadRecord.id == upload_id, media_for_draft(draft_id, include_discarded=True)
            )
        ):
            return False
        reference = await session.get(MediaDraftReference, (draft_id, upload_id))
        if reference is None:
            reference = MediaDraftReference(draft_id=draft_id, upload_id=upload_id)
            session.add(reference)
        reference.discarded = True
        await session.flush()
        if await session.scalar(
            select(MediaUploadRecord.id).where(MediaUploadRecord.id == upload_id, retained_elsewhere(draft_id))
        ):
            return True
        job = row[0]
        if job.status is MediaJobStatus.DISCARDED:
            return True
        job.status = MediaJobStatus.DISCARDED
        job.claimed_at = None
        job.claim_token = None
        job.completed_at = None
        job.dead_at = None
        job.discarded_at = database_now()
        job.last_error = None
        return True

    @override
    async def attach(self, source_id: UUID, target_id: UUID, upload_id: UUID, limits: MediaLimits) -> bool:
        """Share one normalization job, preserving per-draft capacity and discard authority."""
        session = self._session()
        for draft_id in sorted({source_id, target_id}):
            await _lock_mutable_submission_draft(session, draft_id, require_editable=draft_id == target_id)
        owners = tuple(
            await session.scalars(
                select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id.in_((source_id, target_id)))
            )
        )
        if len(set(owners)) != 1:
            raise MediaDraftNotFoundError(target_id)
        upload = await session.scalar(
            select(MediaUploadRecord)
            .where(MediaUploadRecord.id == upload_id, media_for_draft(source_id))
            .with_for_update()
        )
        if upload is None:
            return False
        job = await session.get(MediaNormalizationJobRecord, upload_id)
        if job is None or job.status is MediaJobStatus.DISCARDED:
            return False
        if await session.get(MediaDraftReference, (source_id, upload_id)) is None:
            session.add(MediaDraftReference(draft_id=source_id, upload_id=upload_id))
            await session.flush()
        reference = await session.get(MediaDraftReference, (target_id, upload_id))
        if reference is not None and not reference.discarded:
            return True
        totals = await _active_totals(session, target_id)
        outputs = await session.scalar(
            select(func.coalesce(func.sum(MediaArtifactRecord.byte_size), 0)).where(
                MediaArtifactRecord.upload_id == upload_id,
                MediaArtifactRecord.role != MediaArtifactRole.REPORT,
            )
        )
        candidate = MediaBatchTotals(
            image_count=totals.image_count + int(upload.kind is MediaKind.IMAGE),
            video_count=totals.video_count + int(upload.kind is MediaKind.VIDEO),
            source_bytes=totals.source_bytes + upload.source_byte_size,
            output_bytes=totals.output_bytes + int(outputs or 0),
        )
        if violations := limits.batch_violations(candidate):
            raise MediaLimitExceededError(violations)
        if reference is None:
            session.add(MediaDraftReference(draft_id=target_id, upload_id=upload_id))
        else:
            reference.discarded = False
        await session.execute(
            update(SubmissionDraft)
            .where(SubmissionDraft.id == target_id)
            .values(
                revision=SubmissionDraft.revision + 1,
                status=DraftStatus.EDITING,
                preparation_issues=[],
                preparation_retry_at=None,
                updated_at=func.now(),
            )
        )
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
        session = self._session()
        # Database time, matching the visibility timeout check above.
        claimed_at = await session.scalar(select(database_now()))
        assert claimed_at is not None
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
        await session.flush()
        return tuple(claims)

    @override
    async def heartbeat(self, job: ClaimedMediaJob) -> bool:
        """Renew a current job claim and its crash-recovery publication leases."""
        session = self._session()
        current = await session.scalar(select(MediaNormalizationJobRecord).where(*_claim_filter(job)).with_for_update())
        if current is None:
            return False
        current.claimed_at = database_now()
        await self._publications.renew(job)
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
        session = self._session()
        outcome = cast(CursorResult[Any], await session.execute(statement))
        if outcome.rowcount:
            await self._publications.release_claim(job)
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
        session = self._session()
        await lock_uuid(
            session,
            job.upload.draft_id,
            namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE,
        )
        if not await self.track_artifacts(job, artifacts):
            return False
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
        await self._publications.release_selected(job, artifacts)
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
    async def claim_artifact_cleanup(self, *, limit: int) -> tuple[ArtifactCleanupClaim, ...]:
        return await self._publications.claim_cleanup(limit=limit)

    @override
    async def resolve_artifact_cleanup(self, resolutions: Sequence[tuple[ArtifactCleanupClaim, str | None]]) -> None:
        await self._publications.resolve_cleanups(resolutions)

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
        session = self._session()
        outcome = cast(CursorResult[Any], await session.execute(statement))
        applied = bool(outcome.rowcount)
        return MediaJobFailureOutcome(applied=applied, dead=dead and applied)

    async def _discard_unreferenced(self, *, limit: int) -> None:
        """End jobs whose last draft reference and history lease have both gone away."""
        session = self._session()
        unreferenced = select(MediaUploadRecord.id).where(
            exists(select(MediaDraftReference.upload_id).where(MediaDraftReference.upload_id == MediaUploadRecord.id)),
            ~retained_elsewhere(UUID(int=0)),
        )
        jobs = tuple(
            await session.scalars(
                select(MediaNormalizationJobRecord)
                .where(
                    MediaNormalizationJobRecord.upload_id.in_(unreferenced),
                    MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED,
                )
                .order_by(MediaNormalizationJobRecord.upload_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            # A reference or lease may have committed while we waited for the job lock.
            if await session.scalar(unreferenced.where(MediaUploadRecord.id == job.upload_id)) is None:
                continue
            job.status = MediaJobStatus.DISCARDED
            job.claim_token = None
            job.claimed_at = None
            job.completed_at = None
            job.dead_at = None
            job.discarded_at = database_now()
            job.last_error = None
        await session.flush()

    @override
    async def terminal_sources(self, *, limit: int) -> Sequence[TerminalMediaSource]:
        """List raw objects whose terminal metadata still lacks cleanup confirmation."""
        session = self._session()
        await self._discard_unreferenced(limit=limit)
        rows = (
            await session.execute(
                select(MediaUploadRecord.id, MediaUploadRecord.source_object_key)
                .join(
                    MediaNormalizationJobRecord,
                    MediaNormalizationJobRecord.upload_id == MediaUploadRecord.id,
                )
                .where(
                    MediaUploadRecord.raw_deleted_at.is_(None),
                    ~retained_resource("attachment", MediaUploadRecord.id),
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
        session = self._session()
        outcome = cast(CursorResult[Any], await session.execute(statement))
        return bool(outcome.rowcount)


async def _lock_mutable_submission_draft(
    session: AsyncSession,
    draft_id: UUID,
    *,
    authorization: MediaDraftUploadAuthorization | None = None,
    require_editable: bool = True,
) -> DraftStatus:
    await lock_uuid(session, draft_id, namespace=AdvisoryLockNamespace.SUBMISSION_DRAFT_LIFECYCLE)
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
    if require_editable and DraftStatus(status) not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
        raise MediaDraftStateConflictError(status)
    return DraftStatus(status)


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
                media_for_draft(draft_id),
                MediaNormalizationJobRecord.status.in_(active),
            )
        )
    ).one()
    output_bytes = await session.scalar(
        select(func.coalesce(func.sum(MediaArtifactRecord.byte_size), 0))
        .join(MediaUploadRecord, MediaUploadRecord.id == MediaArtifactRecord.upload_id)
        .join(MediaNormalizationJobRecord, MediaNormalizationJobRecord.upload_id == MediaUploadRecord.id)
        .where(
            media_for_draft(draft_id),
            MediaNormalizationJobRecord.status == MediaJobStatus.COMPLETED,
            MediaArtifactRecord.role != MediaArtifactRole.REPORT,
        )
    )
    return MediaBatchTotals(
        output_bytes=int(output_bytes or 0),
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
    if len(roles) != len(role_set) or role_set - MEDIA_VIDEO_THUMBNAIL_ROLES != expected or not valid_thumbnail:
        msg = f"Completed {kind.value} media requires exactly these artifacts: {sorted(expected)}."
        raise ValueError(msg)
