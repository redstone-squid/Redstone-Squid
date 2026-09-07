"""Shared normalization identity survives independent draft discard and expiry."""

from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.media.application.jobs import MediaJobStatus
from squid.media.domain import MediaLimits
from squid.media.errors import MediaDraftNotFoundError
from squid.media.infrastructure.models import MediaNormalizationJobRecord
from squid.media.infrastructure.repository import PostgresMediaJobRepository
from squid.submissions.domain import DraftStatus
from squid.submissions.infrastructure.models import SubmissionDraft
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from tests.integration.media.test_jobs_repository import completed_artifacts, create_submission_draft, upload


async def test_shared_media_is_normalized_once_and_discarded_independently(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    first, second = uuid4(), uuid4()
    await create_submission_draft(sessions, draft_id=first)
    await create_submission_draft(sessions, draft_id=second)
    async with sessions.begin() as session:
        owner = await session.scalar(select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == first))
        await session.execute(
            update(SubmissionDraft).where(SubmissionDraft.id == second).values(owner_account_id=owner)
        )
    jobs = PostgresMediaJobRepository(sessions)
    source = upload(draft_id=first)
    await jobs.enqueue(source, MediaLimits())
    assert await jobs.attach(first, second, source.id, MediaLimits())
    assert await jobs.attach(first, second, source.id, MediaLimits())
    (claim,) = await jobs.claim(limit=10)
    assert await jobs.complete(claim, completed_artifacts(), MediaLimits())
    assert await jobs.claim(limit=10) == ()
    assert await jobs.discard(first, source.id)
    assert (await jobs.list_for_draft(first))[0].status is MediaJobStatus.DISCARDED
    shared = (await jobs.list_for_draft(second))[0]
    assert shared.status is MediaJobStatus.COMPLETED
    assert shared.upload.id == source.id
    assert shared.upload.draft_id == second
    assert await jobs.discard(second, source.id)
    final = await jobs.get(source.id)
    assert final is not None
    assert final.status is MediaJobStatus.DISCARDED


async def test_expiring_birth_draft_preserves_other_candidate(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    first, second = uuid4(), uuid4()
    await create_submission_draft(sessions, draft_id=first)
    await create_submission_draft(sessions, draft_id=second)
    async with sessions.begin() as session:
        owner = await session.scalar(select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == first))
        await session.execute(
            update(SubmissionDraft).where(SubmissionDraft.id == second).values(owner_account_id=owner)
        )
    jobs = PostgresMediaJobRepository(sessions)
    source = upload(draft_id=first)
    await jobs.enqueue(source, MediaLimits())
    assert await jobs.attach(first, second, source.id, MediaLimits())
    async with sessions.begin() as session:
        await session.execute(
            update(SubmissionDraft)
            .where(SubmissionDraft.id == first)
            .values(
                created_at=Instant.now().subtract(hours=2),
                expires_at=Instant.now().subtract(hours=1),
            )
        )
    assert await PostgresDraftRepository(sessions).expire_due(now=Instant.now()) == 1
    assert (await jobs.list_for_draft(second))[0].status is MediaJobStatus.PENDING
    (claim,) = await jobs.claim(limit=10)
    assert await jobs.complete(claim, completed_artifacts(), MediaLimits())
    async with sessions() as session:
        assert (
            await session.scalar(select(SubmissionDraft.status).where(SubmissionDraft.id == first))
            is DraftStatus.EXPIRED
        )
        assert (
            await session.scalar(
                select(MediaNormalizationJobRecord.status).where(MediaNormalizationJobRecord.upload_id == source.id)
            )
            is MediaJobStatus.COMPLETED
        )


async def test_shared_media_cannot_cross_owners(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    sessions = migrated_session_factory
    first, second = uuid4(), uuid4()
    await create_submission_draft(sessions, draft_id=first)
    await create_submission_draft(sessions, draft_id=second)
    jobs = PostgresMediaJobRepository(sessions)
    source = upload(draft_id=first)
    await jobs.enqueue(source, MediaLimits())
    with pytest.raises(MediaDraftNotFoundError):
        await jobs.attach(first, second, source.id, MediaLimits())
