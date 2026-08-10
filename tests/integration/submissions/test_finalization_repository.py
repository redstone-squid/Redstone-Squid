"""PostgreSQL coverage for atomic finalization and UUID claim fencing."""

from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.persistence.base import Base
from squid.submissions.application import StoredDraft
from squid.submissions.domain import (
    DraftSnapshot,
    DraftStatus,
    FinalizationJobStatus,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
    SubmissionSchematicVisibility,
    SubmissionTargetResult,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.submissions.infrastructure.finalization_models import (
    SubmissionFinalizationJob,
    SubmissionFinalizationResult,
)
from squid.submissions.infrastructure.finalization_repository import PostgresFinalizationJobRepository
from squid.submissions.infrastructure.models import (
    SubmissionDraft,
    SubmissionDraftAccess,
    SubmissionDraftChange,
)
from squid.submissions.infrastructure.repository import PostgresDraftRepository

pytestmark = pytest.mark.asyncio

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000501")
NOW = Instant.parse_iso("2026-08-11T18:00:00Z")
_TABLES: tuple[Table, ...] = (
    cast(Table, Account.__table__),
    cast(Table, SubmissionDraft.__table__),
    cast(Table, SubmissionDraftAccess.__table__),
    cast(Table, SubmissionDraftChange.__table__),
    cast(Table, SubmissionFinalizationJob.__table__),
    cast(Table, SubmissionFinalizationResult.__table__),
)


@pytest.fixture(autouse=True)
async def finalization_tables(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=tuple(reversed(_TABLES)))


@pytest.fixture
async def stored_draft(async_session_factory: async_sessionmaker[AsyncSession]) -> StoredDraft:
    async with async_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    draft = StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=account_id,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="other",
            answers={"source_version": "26.1.2"},
        ),
        origin=SubmissionOrigin.WEB,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )
    await PostgresDraftRepository(async_session_factory).create(draft)
    return draft


def _payload(draft: StoredDraft) -> NormalizedSubmission:
    return NormalizedSubmission(
        source_draft_id=draft.snapshot.id,
        owner_account_id=draft.snapshot.owner_account_id,
        origin=draft.origin,
        schema_id=draft.snapshot.schema_id,
        schema_revision=draft.snapshot.schema_revision,
        category=SubmissionCategory.OTHER,
        display_name="Test build",
        description=None,
        creators=("Builder",),
        capture_dimensions=SubmissionDimensions(3, 4, 5),
        source_version="26.1.2",
        version_compatibility=None,
        taxonomy=SubmissionTaxonomy(restriction_keys=("locational",)),
        schematic_policy=SchematicRightsPolicy(
            visibility=SubmissionSchematicVisibility.REVIEWER_ONLY,
            license=None,
            rights_attested=False,
            include_inventories=True,
            include_free_text=False,
        ),
        completion=None,
        ai_generated=False,
        sponsor_attribution=False,
        artifacts=VerifiedSubmissionArtifacts(),
        details=GeneralSubmissionDetails(),
    )


async def test_enqueue_claim_fence_and_completion_are_atomic(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    payload = _payload(stored_draft)
    expires_at = NOW.add(days=7, days_assumed_24h_ok=True)

    first = await repository.enqueue(stored_draft, payload, now=NOW, expires_at=expires_at)
    replay = await repository.enqueue(
        replace(stored_draft, snapshot=replace(stored_draft.snapshot, status=DraftStatus.PROCESSING)),
        payload,
        now=NOW,
        expires_at=expires_at,
    )
    (claim,) = await repository.claim(now=NOW, limit=1)
    stale = replace(claim, claim_token=UUID("00000000-0000-4000-8000-000000000599"))
    result = SubmissionTargetResult(41, "postgres_builds", {"source_draft_id": str(DRAFT_ID)})

    assert first.job_id == replay.job_id
    assert claim.payload == payload
    assert await repository.complete(stale, result, now=NOW) is False
    assert await repository.complete(claim, result, now=NOW) is True

    completed = await repository.get(DRAFT_ID)
    assert completed is not None
    assert completed.status is FinalizationJobStatus.COMPLETED
    assert completed.result == result
    async with async_session_factory() as session:
        draft_status = await session.scalar(select(SubmissionDraft.status).where(SubmissionDraft.id == DRAFT_ID))
    assert draft_status == DraftStatus.SUBMITTED.value


async def test_preparation_attention_can_be_repaired_without_changing_revision(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    expires_at = NOW.add(days=7, days_assumed_24h_ok=True)
    issue = SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING)

    attention = await repository.record_preparation_attention(
        stored_draft,
        (issue,),
        now=NOW,
        expires_at=expires_at,
    )
    repaired_draft = replace(
        stored_draft,
        snapshot=replace(stored_draft.snapshot, status=DraftStatus.NEEDS_ATTENTION),
    )
    pending = await repository.enqueue(
        repaired_draft,
        _payload(repaired_draft),
        now=NOW.add(seconds=1),
        expires_at=expires_at.add(seconds=1),
    )

    assert attention.status is FinalizationJobStatus.NEEDS_ATTENTION
    assert attention.issues == (issue,)
    assert pending.job_id == attention.job_id
    assert pending.status is FinalizationJobStatus.PENDING
    assert pending.issues == ()


async def test_unexpected_failures_dead_letter_without_deleting_draft(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    expires_at = NOW.add(days=7, days_assumed_24h_ok=True)
    await repository.enqueue(stored_draft, _payload(stored_draft), now=NOW, expires_at=expires_at)
    (first,) = await repository.claim(now=NOW, limit=1)

    retry = await repository.fail(
        first,
        "RuntimeError",
        now=NOW,
        retry_at=NOW.add(seconds=2),
        expires_at=expires_at,
        max_attempts=2,
    )
    assert retry.dead is False
    assert await repository.claim(now=NOW.add(seconds=1), limit=1) == ()
    (second,) = await repository.claim(now=NOW.add(seconds=2), limit=1)
    dead = await repository.fail(
        second,
        "RuntimeError",
        now=NOW.add(seconds=2),
        retry_at=NOW.add(seconds=4),
        expires_at=expires_at,
        max_attempts=2,
    )

    assert dead.dead is True
    snapshot = await repository.get(DRAFT_ID)
    assert snapshot is not None
    assert snapshot.status is FinalizationJobStatus.DEAD
    assert snapshot.issues == (SubmissionAttentionIssue("submission", SubmissionAttentionReason.RETRY_EXHAUSTED),)
    async with async_session_factory() as session:
        draft = await session.get(SubmissionDraft, DRAFT_ID)
    assert draft is not None
    assert draft.status == DraftStatus.NEEDS_ATTENTION.value
