"""Submission execution commits builds, receipts, and events under the current job claim."""

from dataclasses import replace
from functools import partial
from typing import override
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.domain import BuildCategory
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.events.models import DomainEvent
from squid.media.domain import MediaKind
from squid.media.domain.jobs import MediaJobStatus
from squid.media.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.messages.domain import MessageRef, MessageSnapshot
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain import Decision, Reason
from squid.permissions.errors import require_decisions
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.application import ClaimedFinalizationJob, StoredDraft
from squid.submissions.application.commit import BuildSubmissionExecutor
from squid.submissions.domain import (
    DraftSnapshot,
    FinalizationJobStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionOrigin,
)
from squid.submissions.infrastructure import finalization_repository as commit_module
from squid.submissions.infrastructure.finalization_repository import (
    PostgresFinalizationJobRepository,
)
from squid.submissions.models.finalization import SubmissionFinalizationResult, SubmissionReceiptMedia
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact
from tests.support.submission_targets import normalized_submission, seed_account_and_version, submission_build


class PreparedBuilds:
    async def prepare(self, submission: NormalizedSubmission) -> Build:
        return submission_build(BuildCategory.OTHER, submission.owner_account_id, draft_id=submission.source_draft_id)


async def claimed_submission(
    sessions: async_sessionmaker[AsyncSession], *, with_media: bool = False
) -> ClaimedFinalizationJob:
    """Persist a draft and finalization claim, optionally seeding completed media metadata."""
    owner = await seed_account_and_version(sessions)
    now = Instant.now()
    draft_id = UUID(int=991)
    draft = StoredDraft(
        snapshot=DraftSnapshot(draft_id, owner, "build_submission.v1", 1, "other"),
        origin=SubmissionOrigin.WEB,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    await transact(
        partial(SqlAlchemyUnitOfWork, sessions), lambda uow: uow.submission_drafts.create(draft), commit=True
    )
    jobs = partial(SqlAlchemyUnitOfWork, sessions)
    payload = normalized_submission(owner, draft_id, "Java 1.21.0")
    if with_media:
        upload_id = UUID(int=992)
        async with build_edit_session(sessions) as session:
            session.add(
                MediaUploadRecord(
                    id=upload_id,
                    draft_id=draft_id,
                    kind=MediaKind.IMAGE,
                    source_content_type="image/png",
                    source_byte_size=3,
                    source_sha256="a" * 64,
                    source_object_key=f"media/raw/{upload_id}",
                    strip_audio=False,
                )
            )
            await session.flush()
            session.add(
                MediaNormalizationJobRecord(upload_id=upload_id, status=MediaJobStatus.COMPLETED, completed_at=now)
            )
        payload = replace(payload, artifacts=replace(payload.artifacts, normalized_media_upload_ids=(upload_id,)))
    await transact(
        jobs,
        lambda uow: uow.finalization_jobs.enqueue(draft, payload, now=now, expires_at=draft.expires_at),
        commit=True,
    )
    (claim,) = await transact(jobs, lambda uow: uow.finalization_jobs.claim(now=now, limit=1), commit=True)
    return claim


@pytest.mark.parametrize("with_media", [False, True])
async def test_executor_commits_build_receipt_and_event_once(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    with_media: bool,
) -> None:
    claim = await claimed_submission(migrated_session_factory, with_media=with_media)
    executor = BuildSubmissionExecutor(
        PreparedBuilds(), uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    )
    result = await executor.execute(claim, now=Instant.now())
    assert isinstance(result, FinalizedBuild)
    assert await executor.execute(claim, now=Instant.now()) is None
    async with migrated_session_factory() as session:
        receipt = await session.get(SubmissionFinalizationResult, claim.job_id)
        assert receipt is not None
        assert receipt.build_id == result.build_id
        assert await session.scalar(select(func.count()).select_from(SubmissionReceiptMedia)) == int(with_media)
        assert await session.scalar(select(func.count()).select_from(Build)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(
                    DomainEvent.event_type == "build.submitted",
                )
            )
            == 1
        )


async def test_receipt_failure_rolls_back_build_and_events(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = await claimed_submission(migrated_session_factory)
    executor = BuildSubmissionExecutor(
        PreparedBuilds(), uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    )

    # Fail after build persistence to verify that the enclosing transaction rolls it back.
    async def fail_receipt(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("receipt unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(commit_module, "_complete", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt unavailable"):
            await executor.execute(claim, now=Instant.now())
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Build)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(
                    DomainEvent.event_type == "build.submitted",
                )
            )
            == 0
        )
        assert await session.get(SubmissionFinalizationResult, claim.job_id) is None
    job = await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory), lambda uow: uow.finalization_jobs.get(claim.draft_id)
    )
    assert job is not None
    assert job.status is FinalizationJobStatus.CLAIMED
    assert isinstance(await executor.execute(claim, now=Instant.now()), FinalizedBuild)


async def test_reclaimed_worker_cannot_create_a_build(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    claim = await claimed_submission(migrated_session_factory)
    jobs = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (replacement,) = await transact(
        jobs, lambda uow: uow.finalization_jobs.claim(now=claim.claimed_at.add(minutes=6), limit=1), commit=True
    )
    executor = BuildSubmissionExecutor(
        PreparedBuilds(), uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    )
    assert await executor.execute(claim, now=Instant.now()) is None
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Build)) == 0
    assert isinstance(await executor.execute(replacement, now=Instant.now()), FinalizedBuild)


async def test_staff_permission_is_rechecked_after_build_preparation(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import update

    from squid.accounts.models import Account
    from squid.permissions.domain import PermissionNode
    from squid.submissions.application import CheckedInFormManifestRegistry, SubmissionDraftService
    from squid.submissions.domain import BuildSubmissionRejected, SubmissionAttentionReason
    from squid.submissions.models.finalization import SubmissionFinalizationJob

    original = await claimed_submission(migrated_session_factory)
    async with build_edit_session(migrated_session_factory) as session:
        staff = Account()
        session.add(staff)
        await session.flush()
        await session.execute(
            update(SubmissionFinalizationJob)
            .where(
                SubmissionFinalizationJob.id == original.job_id,
            )
            .values(requested_by_account_id=staff.id)
        )
    (claim,) = await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory),
        lambda uow: uow.finalization_jobs.claim(now=original.claimed_at.add(minutes=6), limit=1),
        commit=True,
    )

    class Permissions:
        allowed = True

        async def require_in(self, uow: PermissionUnitOfWork, subject: Actor, *nodes: PermissionNode | str) -> None:
            require_decisions(tuple(Decision(str(node), allowed=self.allowed, reason=Reason.DEFAULT) for node in nodes))

    permissions = Permissions()

    class RevokingPreparation(PreparedBuilds):
        @override
        async def prepare(self, submission: NormalizedSubmission) -> Build:
            permissions.allowed = False
            return await super().prepare(submission)

    drafts = SubmissionDraftService(
        CheckedInFormManifestRegistry(),
        permissions=permissions,
        uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory),
    )
    executor = BuildSubmissionExecutor(
        RevokingPreparation(), drafts=drafts, uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    )
    result = await executor.execute(claim, now=Instant.now())
    assert isinstance(result, BuildSubmissionRejected)
    assert result.issues[0].reason is SubmissionAttentionReason.PERMISSION_REVOKED
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Build)) == 0


async def test_merging_a_staff_requester_preserves_owner_and_fences_its_claim(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import update

    from squid.submissions.models.entities import SubmissionDraft
    from squid.submissions.models.finalization import SubmissionFinalizationJob

    claim = await claimed_submission(migrated_session_factory)
    accounts = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    staff = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    survivor = await transact(accounts, lambda uow: uow.accounts.create(), commit=True)
    assert staff.id is not None
    assert survivor.id is not None
    async with build_edit_session(migrated_session_factory) as session:
        await session.execute(
            update(SubmissionFinalizationJob)
            .where(
                SubmissionFinalizationJob.id == claim.job_id,
            )
            .values(requested_by_account_id=staff.id)
        )
        await session.execute(
            update(SubmissionDraft)
            .where(
                SubmissionDraft.id == claim.draft_id,
            )
            .values(submission_actor_account_id=staff.id)
        )
    claim = replace(claim, requested_by_account_id=staff.id)
    await transact(accounts, lambda uow: uow.accounts.merge(survivor.id, staff.id), commit=True)
    jobs = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    async with build_edit_session(migrated_session_factory) as session:
        assert (
            await PostgresFinalizationJobRepository(lambda: session).complete(
                claim, FinalizedBuild(99), now=Instant.now()
            )
            is False
        )
    (replacement,) = await transact(
        jobs, lambda uow: uow.finalization_jobs.claim(now=Instant.now(), limit=1), commit=True
    )
    assert replacement.requested_by_account_id == survivor.id
    assert replacement.payload.owner_account_id == claim.payload.owner_account_id
    assert replacement.claim_token != claim.claim_token
    draft = await transact(
        partial(SqlAlchemyUnitOfWork, migrated_session_factory), lambda uow: uow.submission_drafts.get(claim.draft_id)
    )
    assert draft is not None
    assert draft.snapshot.owner_account_id == claim.payload.owner_account_id
    assert draft.submission_actor_account_id == survivor.id


async def test_source_messages_commit_with_build_and_survive_run_retention(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from pydantic import TypeAdapter

    from squid.submissions.models.entities import SubmissionDraft

    sessions = migrated_session_factory
    claim = await claimed_submission(sessions)
    source = MessageSnapshot(123456, channel_id=456, author_id=789, content="Original inferred facts")
    async with build_edit_session(sessions) as session:
        draft = await session.get(SubmissionDraft, claim.draft_id)
        assert draft is not None
        draft.source_messages = TypeAdapter(tuple[MessageSnapshot, ...]).dump_python((source,), mode="json")
    builds = partial(SqlAlchemyUnitOfWork, sessions)
    result = await BuildSubmissionExecutor(
        PreparedBuilds(), uow_factory=partial(SqlAlchemyUnitOfWork, sessions)
    ).execute(claim, now=Instant.now())
    assert isinstance(result, FinalizedBuild)
    saved = await transact(builds, lambda uow: uow.builds.get_by_id(result.build_id))
    assert saved is not None
    assert saved.source_messages == (MessageRef(123456, channel_id=456),)
    stored = await transact(builds, lambda uow: uow.messages.get_by_id(123456))
    assert stored is not None
    assert stored.content == "Original inferred facts"


async def test_executor_recovers_a_loaded_source_build(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    claim = await claimed_submission(migrated_session_factory)
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    existing = await PreparedBuilds().prepare(claim.payload)
    await transact(
        repository,
        lambda uow: uow.builds.save(existing),
        commit=True,
    )

    class ExistingBuilds:
        async def prepare(self, submission: NormalizedSubmission) -> Build:
            loaded = await transact(
                repository, lambda uow: uow.builds.get_by_source_submission_draft_id(submission.source_draft_id)
            )
            assert loaded is not None
            return loaded

    executor = BuildSubmissionExecutor(
        ExistingBuilds(), uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    )
    result = await executor.execute(claim, now=Instant.now())
    assert isinstance(result, FinalizedBuild)
    assert result.build_id == existing.id
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Build)) == 1
        receipt = await session.get(SubmissionFinalizationResult, claim.job_id)
        assert receipt is not None
        assert receipt.build_id == existing.id
