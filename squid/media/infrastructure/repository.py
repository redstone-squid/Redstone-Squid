"""PostgreSQL metadata and claim-token queue for media normalization."""

from collections.abc import Sequence
from typing import Any, cast, override
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.media.application.jobs import (
    ClaimedMediaJob,
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
from squid.media.domain import MediaKind
from squid.media.infrastructure.models import (
    MediaArtifactRecord,
    MediaNormalizationJobRecord,
    MediaUploadRecord,
)
from squid.persistence.queue import VISIBILITY_TIMEOUT, retry_delay


class PostgresMediaJobRepository(MediaJobRepository):
    """Persist immutable uploads and fence every worker transition on a UUID token."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @override
    async def enqueue(self, upload: MediaUploadMetadata) -> MediaEnqueueOutcome:
        """Insert an upload and pending job, or accept an identical retry."""
        statement = (
            insert(MediaUploadRecord)
            .values(
                id=upload.id,
                draft_id=upload.draft_id,
                kind=upload.kind.value,
                source_content_type=upload.source_content_type,
                source_byte_size=upload.source_byte_size,
                source_sha256=upload.source_sha256,
                source_object_key=upload.source_object_key,
                strip_audio=upload.strip_audio,
            )
            .on_conflict_do_nothing(index_elements=[MediaUploadRecord.id])
            .returning(MediaUploadRecord.id)
        )
        async with self._session_factory.begin() as session:
            created_id = await session.scalar(statement)
            if created_id is not None:
                session.add(MediaNormalizationJobRecord(upload_id=upload.id))
                return MediaEnqueueOutcome(created=True, status=MediaJobStatus.PENDING)
            existing = await session.get(MediaUploadRecord, upload.id)
            if existing is None:
                msg = "Media upload conflict disappeared inside its transaction."
                raise RuntimeError(msg)
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
    async def complete(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> bool:
        """Atomically persist outputs and complete the job if the claim is current."""
        _validate_completed_artifacts(job.upload.kind, artifacts)
        statement = (
            update(MediaNormalizationJobRecord)
            .where(*_claim_filter(job))
            .values(
                status=MediaJobStatus.COMPLETED.value,
                claimed_at=None,
                claim_token=None,
                completed_at=func.now(),
                dead_at=None,
                last_error=None,
            )
            .returning(MediaNormalizationJobRecord.upload_id)
        )
        async with self._session_factory.begin() as session:
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
        return True

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
            )
        else:
            values.update(
                status=MediaJobStatus.PENDING.value,
                available_at=func.now() + retry_delay(attempts),
                completed_at=None,
                dead_at=None,
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
                            (MediaJobStatus.COMPLETED.value, MediaJobStatus.DEAD.value)
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
                MediaNormalizationJobRecord.status.in_((MediaJobStatus.COMPLETED.value, MediaJobStatus.DEAD.value)),
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
