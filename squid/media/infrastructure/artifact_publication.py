"""Durable object publication leases and reference-fenced cleanup."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast
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
    MediaJobStatus,
    StoredMediaArtifact,
)
from squid.media.infrastructure.models import (
    MediaArtifactObjectRecord,
    MediaArtifactPublicationRecord,
    MediaArtifactRecord,
    MediaNormalizationJobRecord,
)
from squid.persistence.queue import retry_delay


@dataclass(frozen=True, slots=True)
class _ClaimedArtifactCleanup:
    object_key: str
    claim_token: UUID


class PostgresArtifactPublicationRepository:
    """Own object rows, publication leases, fencing, and cleanup acknowledgments."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def track(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> bool:
        """Register possible object keys before upload and lease them to a current claim."""
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
                        set_={"expires_at": lease.excluded.expires_at, "renewed_at": func.now()},
                    )
                    await session.execute(lease)
        return current_claim is not None

    async def renew_in_transaction(self, session: AsyncSession, job: ClaimedMediaJob) -> None:
        """Renew every publication lease owned by a current job transaction."""
        await session.execute(
            update(MediaArtifactPublicationRecord)
            .where(
                MediaArtifactPublicationRecord.upload_id == job.upload.id,
                MediaArtifactPublicationRecord.claim_token == job.claim_token,
            )
            .values(expires_at=_publication_expiry(), renewed_at=func.now())
        )

    async def release_claim_in_transaction(self, session: AsyncSession, job: ClaimedMediaJob) -> None:
        """Release every publication lease held by one fenced claim."""
        await session.execute(
            sql_delete(MediaArtifactPublicationRecord).where(
                MediaArtifactPublicationRecord.upload_id == job.upload.id,
                MediaArtifactPublicationRecord.claim_token == job.claim_token,
            )
        )

    async def release_in_transaction(
        self,
        session: AsyncSession,
        job: ClaimedMediaJob,
        artifacts: Sequence[StoredMediaArtifact],
    ) -> None:
        """Release a claim's selected object leases in its caller's transaction."""
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

    async def release(self, job: ClaimedMediaJob, artifacts: Sequence[StoredMediaArtifact]) -> None:
        """Release selected leases after the claim's object-store writes have stopped."""
        async with self._session_factory.begin() as session:
            await self.release_in_transaction(session, job, artifacts)

    async def cleanup(
        self,
        delete: Callable[[str], Awaitable[None]],
        *,
        limit: int,
    ) -> MediaArtifactCleanupOutcome:
        """Claim due keys, perform storage I/O outside transactions, and fence acknowledgments."""
        async with self._session_factory.begin() as session:
            await self._revoke_expired(session, limit=limit)
            await self._reconcile_objects(session)
            live_reference = exists(
                select(MediaArtifactRecord.id)
                .join(
                    MediaNormalizationJobRecord,
                    MediaNormalizationJobRecord.upload_id == MediaArtifactRecord.upload_id,
                )
                .where(
                    MediaArtifactRecord.object_key == MediaArtifactObjectRecord.object_key,
                    MediaNormalizationJobRecord.status != MediaJobStatus.DISCARDED,
                )
                .correlate(MediaArtifactObjectRecord)
            )
            publication_pending = exists(
                select(MediaArtifactPublicationRecord.object_key)
                .where(MediaArtifactPublicationRecord.object_key == MediaArtifactObjectRecord.object_key)
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
        await self._resolve_cleanups(resolutions)
        return MediaArtifactCleanupOutcome(len(claims), deleted, failed)

    async def _resolve_cleanups(
        self,
        resolutions: Sequence[tuple[_ClaimedArtifactCleanup, str | None]],
    ) -> None:
        """Acknowledge a whole cleanup batch in one transaction.

        Deletion is idempotent and an unacknowledged claim expires on its own,
        so a crash here costs a repeated delete rather than a lost object.
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

    async def _reconcile_objects(self, session: AsyncSession) -> None:
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
            .order_by(
                MediaArtifactRecord.object_key,
                MediaArtifactRecord.created_at.desc(),
                MediaArtifactRecord.id.desc(),
            )
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

    async def _revoke_expired(self, session: AsyncSession, *, limit: int) -> None:
        """Fence expired publishers before releasing their objects for deletion."""
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
            if job is not None and job.status is MediaJobStatus.CLAIMED and job.claim_token == claim_token:
                job.status = MediaJobStatus.PENDING
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


def _publication_expiry() -> Instant:
    return Instant.now().add(seconds=int(MEDIA_ARTIFACT_PUBLICATION_LEASE.total_seconds()))


def _claim_filter(job: ClaimedMediaJob) -> tuple[Any, ...]:
    return (
        MediaNormalizationJobRecord.upload_id == job.upload.id,
        MediaNormalizationJobRecord.status == MediaJobStatus.CLAIMED,
        MediaNormalizationJobRecord.claim_token == job.claim_token,
    )
