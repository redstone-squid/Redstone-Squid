"""Real-PostgreSQL coverage for durable media metadata and queue fencing."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.media.application.jobs import (
    MediaArtifactCleanupInProgressError,
    MediaArtifactRole,
    MediaJobStatus,
    MediaUploadConflictError,
    MediaUploadMetadata,
    StoredMediaArtifact,
    TerminalMediaSource,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.media.errors import MediaDraftNotFoundError, MediaDraftStateConflictError, MediaLimitExceededError
from squid.media.infrastructure.models import (
    MediaArtifactObjectRecord,
    MediaArtifactPublicationRecord,
    MediaArtifactRecord,
    MediaNormalizationJobRecord,
    MediaUploadRecord,
)
from squid.media.infrastructure.repository import PostgresMediaJobRepository
from squid.persistence.base import Base
from squid.submissions.domain import DraftStatus, SubmissionOrigin
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.repository import PostgresDraftRepository

pytestmark = pytest.mark.asyncio

DRAFT_ID = UUID("84ab2da9-c27e-4d37-98c6-973bcc92f5e4")
OTHER_DRAFT_ID = UUID("95bc3eba-d38f-4e48-a9d7-a84cda03f6e5")
UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")
LIMITS = MediaLimits()
_TABLES: tuple[Table, ...] = (
    cast(Table, Account.__table__),
    cast(Table, SubmissionDraft.__table__),
    cast(Table, MediaUploadRecord.__table__),
    cast(Table, MediaNormalizationJobRecord.__table__),
    cast(Table, MediaArtifactRecord.__table__),
    cast(Table, MediaArtifactObjectRecord.__table__),
    cast(Table, MediaArtifactPublicationRecord.__table__),
)


@pytest.fixture(autouse=True)
async def media_job_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=tuple(reversed(_TABLES)))


@pytest.fixture(autouse=True)
async def submission_draft(
    media_job_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_submission_draft(async_session_factory)


async def create_submission_draft(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    draft_id: UUID = DRAFT_ID,
    status: DraftStatus = DraftStatus.EDITING,
) -> None:
    now = Instant.now()
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(
            SubmissionDraft(
                id=draft_id,
                owner_account_id=account.id,
                schema_id="build_submission.v1",
                schema_revision=1,
                category="other",
                status=status,
                answers={},
                origin=SubmissionOrigin.WEB,
                created_at=now,
                updated_at=now,
                expires_at=now.add(days=7, days_assumed_24h_ok=True),
            )
        )


def upload(
    *,
    source: bytes = b"raw-image",
    upload_id: UUID = UPLOAD_ID,
    draft_id: UUID = DRAFT_ID,
) -> MediaUploadMetadata:
    digest = hashlib.sha256(source).hexdigest()
    return MediaUploadMetadata(
        id=upload_id,
        draft_id=draft_id,
        kind=MediaKind.IMAGE,
        source_content_type="image/jpeg",
        source_byte_size=len(source),
        source_sha256=digest,
        source_object_key=f"media/raw/{upload_id}/{digest}",
        strip_audio=False,
    )


def completed_artifacts() -> tuple[StoredMediaArtifact, ...]:
    output = b"normalized"
    report = b'{"schema_version":1}'
    return (
        StoredMediaArtifact(
            role=MediaArtifactRole.OUTPUT,
            object_key=f"media/normalized/aa/{hashlib.sha256(output).hexdigest()}",
            content_type="image/png",
            byte_size=len(output),
            sha256=hashlib.sha256(output).hexdigest(),
            width=2,
            height=2,
        ),
        StoredMediaArtifact(
            role=MediaArtifactRole.REPORT,
            object_key=f"media/reports/bb/{hashlib.sha256(report).hexdigest()}",
            content_type="application/json",
            byte_size=len(report),
            sha256=hashlib.sha256(report).hexdigest(),
            width=None,
            height=None,
        ),
    )


async def test_identical_enqueue_is_retry_safe_but_mismatched_metadata_conflicts(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    metadata = upload()

    assert (await repository.enqueue(metadata, LIMITS)).created is True
    assert (await repository.enqueue(metadata, LIMITS)).created is False
    with pytest.raises(MediaUploadConflictError):
        await repository.enqueue(upload(source=b"different"), LIMITS)

    snapshot = await repository.get(UPLOAD_ID)
    assert snapshot is not None
    assert snapshot.status is MediaJobStatus.PENDING
    assert snapshot.upload.source_sha256 == metadata.source_sha256


async def test_reclaim_uses_a_new_token_and_stale_worker_cannot_complete(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (first,) = await repository.claim(limit=1)
    assert await repository.claim(limit=1) == ()

    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == UPLOAD_ID)
            .values(claimed_at=first.claimed_at.subtract(minutes=6))
        )
    (second,) = await repository.claim(limit=1)

    assert second.claim_token != first.claim_token
    assert await repository.complete(first, completed_artifacts(), LIMITS) is False
    assert await repository.complete(second, completed_artifacts(), LIMITS) is True
    snapshot = await repository.get(UPLOAD_ID)
    assert snapshot is not None
    assert snapshot.status is MediaJobStatus.COMPLETED
    assert snapshot.claim_token is None
    assert {artifact.role for artifact in snapshot.artifacts} == {
        MediaArtifactRole.OUTPUT,
        MediaArtifactRole.REPORT,
    }

    assert tuple(await repository.terminal_sources(limit=10)) == (
        TerminalMediaSource(UPLOAD_ID, upload().source_object_key),
    )


async def test_heartbeat_keeps_long_media_work_owned_and_fences_an_old_token(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (first,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.track_artifacts(first, artifacts)
    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == UPLOAD_ID)
            .values(claimed_at=first.claimed_at.subtract(minutes=6))
        )

    assert await repository.heartbeat(first)
    assert await repository.claim(limit=1) == ()
    async with async_session_factory() as session:
        expiries = tuple((await session.scalars(select(MediaArtifactPublicationRecord.expires_at))).all())
    assert len(expiries) == 2
    assert all(expiry > Instant.now().add(hours=47) for expiry in expiries)

    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == UPLOAD_ID)
            .values(claimed_at=first.claimed_at.subtract(minutes=6))
        )
    (second,) = await repository.claim(limit=1)
    assert await repository.heartbeat(first) is False
    assert await repository.complete(first, artifacts, LIMITS) is False
    assert await repository.heartbeat(second)


async def test_cleanup_preserves_shared_objects_until_every_reference_is_discarded(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    first = upload()
    second = upload(upload_id=uuid4(), source=b"another raw image")
    await repository.enqueue(first, LIMITS)
    (first_claim,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.complete(first_claim, artifacts, LIMITS)
    await repository.enqueue(second, LIMITS)
    (second_claim,) = await repository.claim(limit=1)
    assert await repository.complete(second_claim, artifacts, LIMITS)
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        deleted.append(object_key)

    assert await repository.discard(DRAFT_ID, first.id)
    still_referenced = await repository.cleanup_artifacts(delete, limit=10)
    assert (still_referenced.attempted, still_referenced.deleted, still_referenced.failed) == (0, 0, 0)
    assert deleted == []

    assert await repository.discard(DRAFT_ID, second.id)
    unreferenced = await repository.cleanup_artifacts(delete, limit=10)
    assert (unreferenced.attempted, unreferenced.deleted, unreferenced.failed) == (2, 2, 0)
    assert set(deleted) == {artifact.object_key for artifact in artifacts}
    snapshots = await repository.list_for_draft(DRAFT_ID)
    assert all(snapshot.status is MediaJobStatus.DISCARDED for snapshot in snapshots)
    assert all(snapshot.artifacts == artifacts for snapshot in snapshots)


async def test_artifact_cleanup_failure_is_durably_retried(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (claim,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.track_artifacts(claim, artifacts)
    assert await repository.track_artifacts(claim, artifacts)
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MediaArtifactPublicationRecord)) == 2
    assert (await repository.fail(claim, "invalid", max_attempts=1, terminal=True)).dead
    await repository.release_artifacts(claim, artifacts)

    async def unavailable(_object_key: str) -> None:
        raise OSError("storage unavailable")

    failed = await repository.cleanup_artifacts(unavailable, limit=10)
    assert (failed.attempted, failed.deleted, failed.failed) == (2, 0, 2)
    async with async_session_factory() as session:
        rows = tuple((await session.scalars(select(MediaArtifactObjectRecord))).all())
    assert {row.attempts for row in rows} == {1}
    assert {row.last_error for row in rows} == {"OSError"}
    assert all(row.deleted_at is None for row in rows)

    async with async_session_factory.begin() as session:
        await session.execute(update(MediaArtifactObjectRecord).values(available_at=func.now()))
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        deleted.append(object_key)

    retried = await repository.cleanup_artifacts(delete, limit=10)
    assert (retried.attempted, retried.deleted, retried.failed) == (2, 2, 0)
    assert set(deleted) == {artifact.object_key for artifact in artifacts}


async def test_publication_lease_survives_discard_until_the_writer_releases_it(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    metadata = upload()
    await repository.enqueue(metadata, LIMITS)
    (claim,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.track_artifacts(claim, artifacts)
    assert await repository.discard(DRAFT_ID, metadata.id)
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        deleted.append(object_key)

    while_put_is_in_flight = await repository.cleanup_artifacts(delete, limit=10)
    assert while_put_is_in_flight.attempted == 0
    assert deleted == []

    await repository.release_artifacts(claim, artifacts)
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MediaArtifactPublicationRecord)) == 0
    after_put_stopped = await repository.cleanup_artifacts(delete, limit=10)
    assert after_put_stopped.deleted == 2
    assert set(deleted) == {artifact.object_key for artifact in artifacts}


async def test_crashed_publication_lease_expires_for_eventual_cleanup(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    metadata = upload()
    await repository.enqueue(metadata, LIMITS)
    (claim,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.track_artifacts(claim, artifacts)
    assert await repository.discard(DRAFT_ID, metadata.id)

    async def delete(_object_key: str) -> None:
        pass

    assert (await repository.cleanup_artifacts(delete, limit=10)).attempted == 0
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MediaArtifactPublicationRecord)) == 2
    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaArtifactPublicationRecord).values(
                created_at=func.now() - timedelta(days=2),
                expires_at=func.now() - timedelta(days=1),
            )
        )
    expired = await repository.cleanup_artifacts(delete, limit=10)
    assert expired.deleted == 2
    async with async_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MediaArtifactPublicationRecord)) == 0


async def test_expired_publication_revokes_paused_worker_before_deleting_partial_objects(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (paused,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.track_artifacts(paused, artifacts)
    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaArtifactPublicationRecord).values(
                created_at=func.now() - timedelta(days=3),
                expires_at=func.now() - timedelta(days=1),
            )
        )
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        snapshot = await repository.get(UPLOAD_ID)
        assert snapshot is not None
        assert snapshot.status is MediaJobStatus.PENDING
        assert snapshot.claim_token is None
        assert snapshot.attempts == 0
        deleted.append(object_key)

    cleanup = await repository.cleanup_artifacts(delete, limit=10)

    assert cleanup.deleted == 2
    assert set(deleted) == {artifact.object_key for artifact in artifacts}
    assert await repository.heartbeat(paused) is False
    assert await repository.complete(paused, artifacts, LIMITS) is False
    assert await repository.track_artifacts(paused, artifacts) is False

    deleted.clear()
    cleanup = await repository.cleanup_artifacts(delete, limit=10)
    assert cleanup.deleted == 2
    assert set(deleted) == {artifact.object_key for artifact in artifacts}


async def test_bounded_lease_pruning_never_exposes_an_unprocessed_publisher(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    first = upload()
    second = upload(upload_id=uuid4(), source=b"second raw image")
    artifacts = completed_artifacts()
    await repository.enqueue(first, LIMITS)
    (first_claim,) = await repository.claim(limit=1)
    assert await repository.track_artifacts(first_claim, artifacts)
    assert (await repository.fail(first_claim, "crashed", max_attempts=1, terminal=True)).dead
    await repository.enqueue(second, LIMITS)
    (second_claim,) = await repository.claim(limit=1)
    assert await repository.track_artifacts(second_claim, artifacts)
    assert (await repository.fail(second_claim, "crashed", max_attempts=1, terminal=True)).dead
    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaArtifactPublicationRecord).values(
                created_at=func.now() - timedelta(days=3),
                expires_at=func.now() - timedelta(days=1),
            )
        )
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        deleted.append(object_key)

    assert (await repository.cleanup_artifacts(delete, limit=1)).attempted == 0
    assert deleted == []
    assert (await repository.cleanup_artifacts(delete, limit=1)).deleted == 1
    assert len(deleted) == 1


async def test_cleanup_claim_prevents_publication_while_storage_delete_is_in_flight(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    stale = upload()
    publisher = upload(upload_id=uuid4(), source=b"publisher raw image")
    artifacts = completed_artifacts()
    await repository.enqueue(stale, LIMITS)
    (stale_claim,) = await repository.claim(limit=1)
    assert await repository.track_artifacts(stale_claim, artifacts)
    assert (await repository.fail(stale_claim, "invalid", max_attempts=1, terminal=True)).dead
    await repository.release_artifacts(stale_claim, artifacts)
    await repository.enqueue(publisher, LIMITS)
    (publisher_claim,) = await repository.claim(limit=1)
    delete_started = asyncio.Event()
    allow_delete = asyncio.Event()

    async def blocking_delete(_object_key: str) -> None:
        delete_started.set()
        await allow_delete.wait()

    cleanup_task = asyncio.create_task(repository.cleanup_artifacts(blocking_delete, limit=10))
    await asyncio.wait_for(delete_started.wait(), timeout=1)
    with pytest.raises(MediaArtifactCleanupInProgressError) as exc_info:
        await repository.track_artifacts(publisher_claim, artifacts)
    assert await repository.defer(publisher_claim, until=exc_info.value.retry_at)
    deferred = await repository.get(publisher.id)
    assert deferred is not None
    assert deferred.status is MediaJobStatus.PENDING
    assert deferred.attempts == 0
    assert deferred.available_at == exc_info.value.retry_at

    allow_delete.set()
    assert (await cleanup_task).deleted == 2
    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == publisher.id)
            .values(available_at=func.now())
        )
    (retry,) = await repository.claim(limit=1)
    assert retry.attempts == 0
    assert await repository.track_artifacts(retry, artifacts)
    await repository.release_artifacts(retry, artifacts)


async def test_stale_put_observed_during_delete_keeps_the_object_due_for_cleanup(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (stale_claim,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    assert await repository.track_artifacts(stale_claim, artifacts)
    assert (await repository.fail(stale_claim, "invalid", max_attempts=1, terminal=True)).dead
    await repository.release_artifacts(stale_claim, artifacts)
    delete_started = asyncio.Event()
    allow_delete = asyncio.Event()

    async def blocking_delete(_object_key: str) -> None:
        delete_started.set()
        await allow_delete.wait()

    cleanup_task = asyncio.create_task(repository.cleanup_artifacts(blocking_delete, limit=10))
    await asyncio.wait_for(delete_started.wait(), timeout=1)
    assert await repository.track_artifacts(stale_claim, artifacts) is False
    allow_delete.set()
    assert (await cleanup_task).deleted == 2

    async with async_session_factory() as session:
        objects = tuple((await session.scalars(select(MediaArtifactObjectRecord))).all())
    assert all(row.deleted_at is None for row in objects)
    assert all(row.cleanup_claim_token is None for row in objects)
    assert (await repository.cleanup_artifacts(lambda _key: asyncio.sleep(0), limit=10)).deleted == 2


async def test_reconciliation_discovers_artifacts_committed_by_an_old_worker(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (_claim,) = await repository.claim(limit=1)
    artifacts = completed_artifacts()
    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == UPLOAD_ID)
            .values(
                status=MediaJobStatus.COMPLETED.value,
                claimed_at=None,
                claim_token=None,
                completed_at=func.now(),
            )
        )
        session.add_all(
            MediaArtifactRecord(
                upload_id=UPLOAD_ID,
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
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        deleted.append(object_key)

    reconciled = await repository.cleanup_artifacts(delete, limit=10)
    assert reconciled.attempted == 0
    assert deleted == []
    async with async_session_factory() as session:
        objects = tuple((await session.scalars(select(MediaArtifactObjectRecord))).all())
    assert {row.object_key for row in objects} == {artifact.object_key for artifact in artifacts}
    assert all(row.available_at > Instant.now().add(hours=47) for row in objects)

    assert await repository.discard(DRAFT_ID, UPLOAD_ID)
    assert (await repository.cleanup_artifacts(delete, limit=10)).attempted == 0
    async with async_session_factory.begin() as session:
        await session.execute(update(MediaArtifactObjectRecord).values(available_at=func.now()))
    assert (await repository.cleanup_artifacts(delete, limit=10)).deleted == 2
    assert set(deleted) == {artifact.object_key for artifact in artifacts}


async def test_retry_backoff_then_dead_state_and_raw_cleanup_acknowledgment(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    await repository.enqueue(upload(), LIMITS)
    (first,) = await repository.claim(limit=1)

    failure = await repository.fail(first, "transient", max_attempts=2, terminal=False)
    assert (failure.applied, failure.dead) == (True, False)
    pending = await repository.get(UPLOAD_ID)
    assert pending is not None
    assert pending.status is MediaJobStatus.PENDING
    assert pending.attempts == 1

    async with async_session_factory.begin() as session:
        await session.execute(
            update(MediaNormalizationJobRecord)
            .where(MediaNormalizationJobRecord.upload_id == UPLOAD_ID)
            .values(available_at=func.now())
        )
    (second,) = await repository.claim(limit=1)
    failure = await repository.fail(second, "still broken", max_attempts=2, terminal=False)
    assert (failure.applied, failure.dead) == (True, True)

    dead = await repository.get(UPLOAD_ID)
    assert dead is not None
    assert dead.status is MediaJobStatus.DEAD
    assert dead.dead_at is not None
    assert dead.last_error == "still broken"
    (source,) = await repository.terminal_sources(limit=10)
    assert source.upload_id == UPLOAD_ID
    assert await repository.mark_source_deleted(source) is True
    assert await repository.terminal_sources(limit=10) == ()
    cleaned = await repository.get(UPLOAD_ID)
    assert cleaned is not None
    assert cleaned.upload.raw_deleted_at is not None


async def test_draft_capacity_and_discard_are_serialized(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    limits = MediaLimits(max_images=1)
    first = upload()
    second = upload(upload_id=uuid4(), source=b"second")

    await repository.enqueue(first, limits)
    with pytest.raises(MediaLimitExceededError):
        await repository.enqueue(second, limits)

    assert await repository.discard(DRAFT_ID, first.id)
    assert (await repository.enqueue(second, limits)).created
    snapshots = await repository.list_for_draft(DRAFT_ID)
    assert {snapshot.upload.id: snapshot.status for snapshot in snapshots} == {
        first.id: MediaJobStatus.DISCARDED,
        second.id: MediaJobStatus.PENDING,
    }
    assert tuple(await repository.terminal_sources(limit=10)) == (
        TerminalMediaSource(first.id, first.source_object_key),
    )


@pytest.mark.parametrize("status", [DraftStatus.PROCESSING, DraftStatus.SUBMITTED, DraftStatus.EXPIRED])
async def test_real_submission_draft_lifecycle_fences_new_media_and_discard(
    async_session_factory: async_sessionmaker[AsyncSession],
    status: DraftStatus,
) -> None:
    repository = PostgresMediaJobRepository(async_session_factory)
    first = upload()
    await repository.enqueue(first, LIMITS)
    async with async_session_factory.begin() as session:
        await session.execute(update(SubmissionDraft).where(SubmissionDraft.id == DRAFT_ID).values(status=status.value))

    replay = await repository.enqueue(first, LIMITS)
    assert replay.created is False
    with pytest.raises(MediaDraftStateConflictError) as enqueue_error:
        await repository.enqueue(upload(upload_id=uuid4(), source=b"new"), LIMITS)
    with pytest.raises(MediaDraftStateConflictError) as discard_error:
        await repository.discard(DRAFT_ID, first.id)

    assert enqueue_error.value.public_context == {"reason": "draft_state", "status": status.value}
    assert discard_error.value.public_context == {"reason": "draft_state", "status": status.value}
    snapshot = await repository.get(first.id)
    assert snapshot is not None
    assert snapshot.status is MediaJobStatus.PENDING


async def test_delete_tombstones_media_and_post_delete_mutations_fail_closed(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    media = PostgresMediaJobRepository(async_session_factory)
    drafts = PostgresDraftRepository(async_session_factory)
    metadata = upload()
    await media.enqueue(metadata, LIMITS)
    async with async_session_factory() as session:
        account_id = await session.scalar(
            select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == DRAFT_ID)
        )
    assert account_id is not None

    assert await drafts.delete_owned(DRAFT_ID, account_id) is True

    snapshot = await media.get(metadata.id)
    assert snapshot is not None
    assert snapshot.status is MediaJobStatus.DISCARDED
    assert tuple(await media.terminal_sources(limit=10)) == (
        TerminalMediaSource(metadata.id, metadata.source_object_key),
    )
    with pytest.raises(MediaDraftNotFoundError):
        await media.enqueue(upload(upload_id=uuid4(), source=b"new"), LIMITS)
    with pytest.raises(MediaDraftNotFoundError):
        await media.discard(DRAFT_ID, metadata.id)


async def test_concurrent_delete_and_enqueue_never_leave_live_or_untracked_media(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    media = PostgresMediaJobRepository(async_session_factory)
    drafts = PostgresDraftRepository(async_session_factory)
    metadata = upload()
    async with async_session_factory() as session:
        account_id = await session.scalar(
            select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == DRAFT_ID)
        )
    assert account_id is not None

    enqueue_result, delete_result = await asyncio.gather(
        media.enqueue(metadata, LIMITS),
        drafts.delete_owned(DRAFT_ID, account_id),
        return_exceptions=True,
    )

    assert delete_result is True
    if isinstance(enqueue_result, BaseException):
        assert isinstance(enqueue_result, MediaDraftNotFoundError)
        assert await media.get(metadata.id) is None
    else:
        assert enqueue_result.created is True
        snapshot = await media.get(metadata.id)
        assert snapshot is not None
        assert snapshot.status is MediaJobStatus.DISCARDED


async def test_deleting_completed_media_retains_normalized_keys_in_tombstone(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    media = PostgresMediaJobRepository(async_session_factory)
    drafts = PostgresDraftRepository(async_session_factory)
    metadata = upload()
    await media.enqueue(metadata, LIMITS)
    (claim,) = await media.claim(limit=1)
    artifacts = completed_artifacts()
    assert await media.complete(claim, artifacts, LIMITS) is True
    async with async_session_factory() as session:
        account_id = await session.scalar(
            select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == DRAFT_ID)
        )
    assert account_id is not None

    assert await drafts.delete_owned(DRAFT_ID, account_id) is True

    snapshot = await media.get(metadata.id)
    assert snapshot is not None
    assert snapshot.status is MediaJobStatus.DISCARDED
    assert {artifact.object_key for artifact in snapshot.artifacts} == {artifact.object_key for artifact in artifacts}


async def test_deleting_one_draft_keeps_objects_referenced_by_another_draft(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    media = PostgresMediaJobRepository(async_session_factory)
    drafts = PostgresDraftRepository(async_session_factory)
    await create_submission_draft(async_session_factory, draft_id=OTHER_DRAFT_ID)
    first = upload()
    second = upload(upload_id=uuid4(), draft_id=OTHER_DRAFT_ID, source=b"second-source")
    artifacts = completed_artifacts()
    await media.enqueue(first, LIMITS)
    await media.enqueue(second, LIMITS)
    first_claim, second_claim = await media.claim(limit=2)
    claims = {claim.upload.id: claim for claim in (first_claim, second_claim)}
    assert await media.complete(claims[first.id], artifacts, LIMITS)
    assert await media.complete(claims[second.id], artifacts, LIMITS)
    async with async_session_factory() as session:
        account_id = await session.scalar(
            select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == DRAFT_ID)
        )
    assert account_id is not None

    assert await drafts.delete_owned(DRAFT_ID, account_id)
    deleted: list[str] = []

    async def delete(object_key: str) -> None:
        deleted.append(object_key)

    cleanup = await media.cleanup_artifacts(delete, limit=10)
    retained = await media.get(second.id)

    assert cleanup.attempted == 0
    assert deleted == []
    assert retained is not None
    assert retained.status is MediaJobStatus.COMPLETED
    assert {artifact.object_key for artifact in retained.artifacts} == {artifact.object_key for artifact in artifacts}
