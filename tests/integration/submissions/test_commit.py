"""PostgreSQL transaction and claim-fence coverage for submission execution."""

from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.builds.domain import Build, BuildCategory
from squid.builds.infrastructure.models import Build as SQLBuild
from squid.builds.infrastructure.repository import BuildRepository
from squid.events.infrastructure.models import DomainEventRecord
from squid.media.application.jobs import MediaJobStatus
from squid.media.domain import MediaKind
from squid.media.infrastructure.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.submissions.application import ClaimedFinalizationJob, StoredDraft
from squid.submissions.domain import (
    DraftSnapshot,
    FinalizationJobStatus,
    FinalizedBuild,
    NormalizedSubmission,
    SubmissionOrigin,
)
from squid.submissions.infrastructure import commit as commit_module
from squid.submissions.infrastructure.commit import PostgresSubmissionExecutor
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationResult, SubmissionReceiptMedia
from squid.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from tests.support.submission_targets import normalized_submission, seed_account_and_version, submission_build


class PreparedBuilds:
    async def prepare(self, submission: NormalizedSubmission) -> Build:
        return submission_build(BuildCategory.OTHER, submission.owner_account_id, draft_id=submission.source_draft_id)


async def claimed_submission(
    sessions: async_sessionmaker[AsyncSession], *, with_media: bool = False
) -> ClaimedFinalizationJob:
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
    await PostgresDraftRepository(sessions).create(draft)
    jobs = PostgresFinalizationJobRepository(sessions)
    payload = normalized_submission(owner, draft_id, "Java 1.21.0")
    if with_media:
        upload_id = UUID(int=992)
        async with sessions.begin() as session:
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
    await jobs.enqueue(draft, payload, now=now, expires_at=draft.expires_at)
    (claim,) = await jobs.claim(now=now, limit=1)
    return claim


@pytest.mark.parametrize("with_media", [False, True])
async def test_executor_commits_build_receipt_and_event_once(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    with_media: bool,
) -> None:
    claim = await claimed_submission(migrated_session_factory, with_media=with_media)
    executor = PostgresSubmissionExecutor(
        migrated_session_factory, BuildRepository(migrated_session_factory), PreparedBuilds()
    )
    result = await executor.execute(claim, now=Instant.now())
    assert isinstance(result, FinalizedBuild)
    assert await executor.execute(claim, now=Instant.now()) is None
    async with migrated_session_factory() as session:
        receipt = await session.get(SubmissionFinalizationResult, claim.job_id)
        assert receipt is not None
        assert receipt.build_id == result.build_id
        assert await session.scalar(select(func.count()).select_from(SubmissionReceiptMedia)) == int(with_media)
        assert await session.scalar(select(func.count()).select_from(SQLBuild)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEventRecord)
                .where(
                    DomainEventRecord.event_type == "build.submitted",
                )
            )
            == 1
        )


async def test_receipt_failure_rolls_back_build_and_events(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = await claimed_submission(migrated_session_factory)
    executor = PostgresSubmissionExecutor(
        migrated_session_factory, BuildRepository(migrated_session_factory), PreparedBuilds()
    )

    async def fail_receipt(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("receipt unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(commit_module, "complete_in_session", fail_receipt)
        with pytest.raises(RuntimeError, match="receipt unavailable"):
            await executor.execute(claim, now=Instant.now())
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SQLBuild)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DomainEventRecord)
                .where(
                    DomainEventRecord.event_type == "build.submitted",
                )
            )
            == 0
        )
        assert await session.get(SubmissionFinalizationResult, claim.job_id) is None
    job = await PostgresFinalizationJobRepository(migrated_session_factory).get(claim.draft_id)
    assert job is not None
    assert job.status is FinalizationJobStatus.CLAIMED
    assert isinstance(await executor.execute(claim, now=Instant.now()), FinalizedBuild)


async def test_reclaimed_worker_cannot_create_a_build(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    claim = await claimed_submission(migrated_session_factory)
    jobs = PostgresFinalizationJobRepository(migrated_session_factory)
    (replacement,) = await jobs.claim(now=claim.claimed_at.add(minutes=6), limit=1)
    executor = PostgresSubmissionExecutor(
        migrated_session_factory, BuildRepository(migrated_session_factory), PreparedBuilds()
    )
    assert await executor.execute(claim, now=Instant.now()) is None
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SQLBuild)) == 0
    assert isinstance(await executor.execute(replacement, now=Instant.now()), FinalizedBuild)


async def test_staff_permission_is_rechecked_after_build_preparation(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import update

    from squid.accounts.infrastructure.models import Account
    from squid.permissions.domain import PermissionNode, Subject
    from squid.submissions.application import CheckedInFormManifestRegistry, SubmissionDraftService
    from squid.submissions.domain import BuildSubmissionRejected, SubmissionAttentionReason
    from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob

    original = await claimed_submission(migrated_session_factory)
    async with migrated_session_factory.begin() as session:
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
    (claim,) = await PostgresFinalizationJobRepository(migrated_session_factory).claim(
        now=original.claimed_at.add(minutes=6),
        limit=1,
    )

    class Permissions:
        allowed = True

        async def allows(self, subject: Subject, node: PermissionNode) -> bool:
            return self.allowed

    permissions = Permissions()

    class RevokingPreparation(PreparedBuilds):
        async def prepare(self, submission: NormalizedSubmission) -> Build:
            permissions.allowed = False
            return await super().prepare(submission)

    drafts = SubmissionDraftService(
        PostgresDraftRepository(migrated_session_factory),
        CheckedInFormManifestRegistry(),
        permissions=permissions,
    )
    executor = PostgresSubmissionExecutor(
        migrated_session_factory,
        BuildRepository(migrated_session_factory),
        RevokingPreparation(),
        drafts=drafts,
    )
    result = await executor.execute(claim, now=Instant.now())
    assert isinstance(result, BuildSubmissionRejected)
    assert result.issues[0].reason is SubmissionAttentionReason.PERMISSION_REVOKED
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SQLBuild)) == 0


async def test_merging_a_staff_requester_preserves_owner_and_fences_its_claim(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy import update

    from squid.accounts.infrastructure.repository import AccountRepository
    from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
    from squid.submissions.infrastructure.models import SubmissionDraft

    claim = await claimed_submission(migrated_session_factory)
    accounts = AccountRepository(migrated_session_factory, "test-pepper")
    staff = await accounts.create()
    survivor = await accounts.create()
    assert staff.id is not None
    assert survivor.id is not None
    async with migrated_session_factory.begin() as session:
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
    await accounts.merge(survivor.id, staff.id)
    jobs = PostgresFinalizationJobRepository(migrated_session_factory)
    assert await jobs.complete(claim, FinalizedBuild(99), now=Instant.now()) is False
    (replacement,) = await jobs.claim(now=Instant.now(), limit=1)
    assert replacement.requested_by_account_id == survivor.id
    assert replacement.payload.owner_account_id == claim.payload.owner_account_id
    assert replacement.claim_token != claim.claim_token
    draft = await PostgresDraftRepository(migrated_session_factory).get(claim.draft_id)
    assert draft is not None
    assert draft.snapshot.owner_account_id == claim.payload.owner_account_id
    assert draft.submission_actor_account_id == survivor.id


async def test_source_messages_commit_with_build_and_survive_run_retention(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from pydantic import TypeAdapter

    from squid.builds.domain import SourceMessage
    from squid.submissions.infrastructure.models import SubmissionDraft

    sessions = migrated_session_factory
    claim = await claimed_submission(sessions)
    source = SourceMessage(123456, channel_id=456, author_id=789, content="Original inferred facts")
    async with sessions.begin() as session:
        draft = await session.get(SubmissionDraft, claim.draft_id)
        assert draft is not None
        draft.source_messages = TypeAdapter(tuple[SourceMessage, ...]).dump_python((source,), mode="json")
    builds = BuildRepository(sessions)
    result = await PostgresSubmissionExecutor(sessions, builds, PreparedBuilds()).execute(claim, now=Instant.now())
    assert isinstance(result, FinalizedBuild)
    saved = await builds.get_by_id(result.build_id)
    assert saved is not None
    assert saved.source_messages == (source,)
