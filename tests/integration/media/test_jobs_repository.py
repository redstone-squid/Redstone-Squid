"""Real-PostgreSQL coverage for durable media metadata and queue fencing."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.media.application.jobs import (
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
    MediaArtifactRecord,
    MediaNormalizationJobRecord,
    MediaUploadRecord,
)
from squid.media.infrastructure.repository import PostgresMediaJobRepository
from squid.persistence.base import Base
from squid.submissions.domain import DraftStatus
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.repository import PostgresDraftRepository

pytestmark = pytest.mark.asyncio

DRAFT_ID = UUID("84ab2da9-c27e-4d37-98c6-973bcc92f5e4")
UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")
LIMITS = MediaLimits()
_TABLES: tuple[Table, ...] = (
    cast(Table, Account.__table__),
    cast(Table, SubmissionDraft.__table__),
    cast(Table, MediaUploadRecord.__table__),
    cast(Table, MediaNormalizationJobRecord.__table__),
    cast(Table, MediaArtifactRecord.__table__),
)


@pytest.fixture(autouse=True)
async def media_job_tables(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
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
    status: DraftStatus = DraftStatus.EDITING,
) -> None:
    now = Instant.now()
    async with session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        session.add(
            SubmissionDraft(
                id=DRAFT_ID,
                owner_account_id=account.id,
                schema_id="build_submission.v1",
                schema_revision=1,
                category="other",
                status=status.value,
                answers={},
                origin="web",
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
