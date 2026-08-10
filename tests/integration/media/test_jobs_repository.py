"""Real-PostgreSQL coverage for durable media metadata and queue fencing."""

import hashlib
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Table, func, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.media.application.jobs import (
    MediaArtifactRole,
    MediaJobStatus,
    MediaUploadConflictError,
    MediaUploadMetadata,
    StoredMediaArtifact,
    TerminalMediaSource,
)
from squid.media.domain import MediaKind, MediaLimits
from squid.media.errors import MediaLimitExceededError
from squid.media.infrastructure.models import (
    MediaArtifactRecord,
    MediaNormalizationJobRecord,
    MediaUploadRecord,
)
from squid.media.infrastructure.repository import PostgresMediaJobRepository
from squid.persistence.base import Base

pytestmark = pytest.mark.asyncio

DRAFT_ID = UUID("84ab2da9-c27e-4d37-98c6-973bcc92f5e4")
UPLOAD_ID = UUID("75043a53-05ae-4097-bbf4-4eae1d6b088c")
LIMITS = MediaLimits()
_TABLES: tuple[Table, ...] = (
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
            await connection.run_sync(Base.metadata.drop_all, tables=_TABLES)


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
