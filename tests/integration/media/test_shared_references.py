"""Shared normalization identity survives independent draft discard and expiry."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.media.domain import MediaLimits
from squid.media.domain.jobs import MediaJobStatus
from squid.media.errors import MediaDraftNotFoundError
from squid.media.models import MediaNormalizationJobRecord
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.domain import DraftStatus
from squid.submissions.models.entities import SubmissionDraft
from tests.integration.media.test_jobs_repository import completed_artifacts, create_submission_draft, upload
from tests.support.unit_of_work import transact


async def test_shared_media_is_normalized_once_and_discarded_independently(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    first, second = uuid4(), uuid4()
    await create_submission_draft(sessions, draft_id=first)
    await create_submission_draft(sessions, draft_id=second)
    async with sessions.begin() as session:
        owner = await session.scalar(select(SubmissionDraft.owner_account_id).where(SubmissionDraft.id == first))
        assert owner is not None
        await session.execute(
            update(SubmissionDraft).where(SubmissionDraft.id == second).values(owner_account_id=owner)
        )
    jobs = partial(SqlAlchemyUnitOfWork, sessions)
    source = upload(draft_id=first)
    await transact(jobs, lambda uow: uow.media_jobs.enqueue(source, MediaLimits()), commit=True)
    assert await transact(jobs, lambda uow: uow.media_jobs.attach(first, second, source.id, MediaLimits()), commit=True)
    assert await transact(jobs, lambda uow: uow.media_jobs.attach(first, second, source.id, MediaLimits()), commit=True)
    (claim,) = await transact(jobs, lambda uow: uow.media_jobs.claim(limit=10), commit=True)
    assert await transact(
        jobs, lambda uow: uow.media_jobs.complete(claim, completed_artifacts(), MediaLimits()), commit=True
    )
    assert await transact(jobs, lambda uow: uow.media_jobs.claim(limit=10), commit=True) == ()
    assert await transact(jobs, lambda uow: uow.media_jobs.discard(first, source.id), commit=True)
    assert (await transact(jobs, lambda uow: uow.media_jobs.list_for_draft(first)))[
        0
    ].status is MediaJobStatus.DISCARDED
    shared = (await transact(jobs, lambda uow: uow.media_jobs.list_for_draft(second)))[0]
    assert shared.status is MediaJobStatus.COMPLETED
    assert shared.upload.id == source.id
    assert shared.upload.draft_id == second
    assert await transact(jobs, lambda uow: uow.media_jobs.discard(second, source.id), commit=True)
    # Open drafts keep files they have removed, so undo can restore them.
    kept = await transact(jobs, lambda uow: uow.media_jobs.get(source.id))
    assert kept is not None
    assert kept.status is MediaJobStatus.COMPLETED
    async with sessions.begin() as session:
        await session.execute(update(SubmissionDraft).values(status=DraftStatus.EXPIRED))
    await transact(jobs, lambda uow: uow.media_jobs.terminal_sources(limit=10), commit=True)
    final = await transact(jobs, lambda uow: uow.media_jobs.get(source.id))
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
        assert owner is not None
        await session.execute(
            update(SubmissionDraft).where(SubmissionDraft.id == second).values(owner_account_id=owner)
        )
    jobs = partial(SqlAlchemyUnitOfWork, sessions)
    source = upload(draft_id=first)
    await transact(jobs, lambda uow: uow.media_jobs.enqueue(source, MediaLimits()), commit=True)
    assert await transact(jobs, lambda uow: uow.media_jobs.attach(first, second, source.id, MediaLimits()), commit=True)
    async with sessions.begin() as session:
        await session.execute(
            update(SubmissionDraft)
            .where(SubmissionDraft.id == first)
            .values(
                created_at=Instant.now().subtract(hours=2),
                expires_at=Instant.now().subtract(hours=1),
            )
        )
    assert (
        await transact(
            partial(SqlAlchemyUnitOfWork, sessions),
            lambda uow: uow.submission_drafts.expire_due(now=Instant.now()),
            commit=True,
        )
        == 1
    )
    assert (await transact(jobs, lambda uow: uow.media_jobs.list_for_draft(second)))[0].status is MediaJobStatus.PENDING
    (claim,) = await transact(jobs, lambda uow: uow.media_jobs.claim(limit=10), commit=True)
    assert await transact(
        jobs, lambda uow: uow.media_jobs.complete(claim, completed_artifacts(), MediaLimits()), commit=True
    )
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
    jobs = partial(SqlAlchemyUnitOfWork, sessions)
    source = upload(draft_id=first)
    await transact(jobs, lambda uow: uow.media_jobs.enqueue(source, MediaLimits()), commit=True)
    with pytest.raises(MediaDraftNotFoundError):
        await transact(jobs, lambda uow: uow.media_jobs.attach(first, second, source.id, MediaLimits()), commit=True)
