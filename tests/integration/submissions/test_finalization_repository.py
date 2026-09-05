"""PostgreSQL coverage for atomic finalization and UUID claim fencing."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Table, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.core.errors import DataIntegrityError
from squid.media.application.jobs import MediaJobStatus
from squid.media.domain import MediaKind
from squid.media.infrastructure.models import MediaNormalizationJobRecord, MediaUploadRecord
from squid.persistence.base import Base
from squid.submissions.application import StoredDraft
from squid.submissions.domain import (
    DraftSnapshot,
    DraftStatus,
    FinalizationJobStatus,
    FinalizedBuild,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
    SubmissionSchematicVisibility,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.submissions.errors import DraftArtifactsChangedError, DraftNotFoundError, DraftStateConflictError
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
    cast(Table, MediaUploadRecord.__table__),
    cast(Table, MediaNormalizationJobRecord.__table__),
    cast(Table, SubmissionFinalizationJob.__table__),
    cast(Table, SubmissionFinalizationResult.__table__),
)


@pytest.fixture(autouse=True)
async def finalization_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
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


def _payload(draft: StoredDraft, *media_upload_ids: UUID) -> NormalizedSubmission:
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
        artifacts=VerifiedSubmissionArtifacts(normalized_media_upload_ids=media_upload_ids),
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
    result = FinalizedBuild(41)

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
        legacy_result = (
            await session.execute(
                select(
                    SubmissionFinalizationResult._legacy_target_key,
                    SubmissionFinalizationResult._legacy_provenance,
                ).where(SubmissionFinalizationResult.job_id == first.job_id)
            )
        ).one()
    assert draft_status == DraftStatus.SUBMITTED.value
    assert legacy_result[0] == "postgres_builds"
    assert legacy_result[1] == {
        "source_draft_id": str(DRAFT_ID),
        "owner_account_id": stored_draft.snapshot.owner_account_id,
        "origin": "web",
        "schema_id": "build_submission.v1",
        "schema_revision": 1,
        "source_installation_id": None,
        "sponsor_installation_id": None,
        "normalized_media_upload_ids": [],
        "sanitized_schematic_id": None,
    }


async def test_build_only_reader_accepts_a_legacy_writer_result(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    expires_at = NOW.add(days=7, days_assumed_24h_ok=True)
    pending = await repository.enqueue(stored_draft, _payload(stored_draft), now=NOW, expires_at=expires_at)
    (claim,) = await repository.claim(now=NOW, limit=1)
    assert await repository.complete(claim, FinalizedBuild(41), now=NOW) is True

    async with async_session_factory.begin() as session:
        await session.execute(
            update(SubmissionFinalizationResult)
            .where(SubmissionFinalizationResult.job_id == pending.job_id)
            .values(
                target_key="legacy_postgres_builds",
                provenance={"source_draft_id": str(DRAFT_ID), "legacy_only": True},
            )
        )

    snapshot = await repository.get(DRAFT_ID)

    assert snapshot is not None
    assert snapshot.result == FinalizedBuild(41)


async def test_claim_refuses_a_payload_with_a_conflicting_digest(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    await repository.enqueue(
        stored_draft,
        _payload(stored_draft),
        now=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )
    async with async_session_factory.begin() as session:
        await session.execute(
            update(SubmissionFinalizationJob)
            .where(SubmissionFinalizationJob.draft_id == DRAFT_ID)
            .values(payload_sha256="0" * 64)
        )

    with pytest.raises(DataIntegrityError, match="payload integrity"):
        await repository.claim(now=NOW, limit=1)

    async with async_session_factory() as session:
        job = await session.scalar(
            select(SubmissionFinalizationJob).where(SubmissionFinalizationJob.draft_id == DRAFT_ID)
        )
    assert job is not None
    assert job.status is FinalizationJobStatus.PENDING
    assert job.attempts == 0
    assert job.claim_token is None


async def _store_media(
    session_factory: async_sessionmaker[AsyncSession],
    upload_id: UUID,
    status: MediaJobStatus | None,
) -> None:
    source = b"raw-image"
    async with session_factory.begin() as session:
        session.add(
            MediaUploadRecord(
                id=upload_id,
                draft_id=DRAFT_ID,
                kind=MediaKind.IMAGE,
                source_content_type="image/jpeg",
                source_byte_size=len(source),
                source_sha256="a" * 64,
                source_object_key=f"media/raw/{upload_id}/{'a' * 64}",
                strip_audio=False,
            )
        )
        await session.flush()
        if status is not None:
            session.add(
                MediaNormalizationJobRecord(
                    upload_id=upload_id,
                    status=status,
                    completed_at=NOW if status is MediaJobStatus.COMPLETED else None,
                    dead_at=NOW if status is MediaJobStatus.DEAD else None,
                    discarded_at=NOW if status is MediaJobStatus.DISCARDED else None,
                )
            )


async def test_enqueue_rechecks_exact_completed_media_set_after_assessment(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    media_id = UUID("00000000-0000-4000-8000-000000000511")
    payload_before_concurrent_upload = _payload(stored_draft)
    await _store_media(async_session_factory, media_id, MediaJobStatus.COMPLETED)

    with pytest.raises(DraftArtifactsChangedError):
        await repository.enqueue(
            stored_draft,
            payload_before_concurrent_upload,
            now=NOW,
            expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
        )

    assert await repository.get(DRAFT_ID) is None
    async with async_session_factory() as session:
        status = await session.scalar(select(SubmissionDraft.status).where(SubmissionDraft.id == DRAFT_ID))
    assert status == DraftStatus.EDITING.value


@pytest.mark.parametrize("status", [MediaJobStatus.PENDING, MediaJobStatus.DEAD, MediaJobStatus.DISCARDED, None])
async def test_enqueue_requires_every_payload_media_id_to_remain_completed(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
    status: MediaJobStatus | None,
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    media_id = UUID("00000000-0000-4000-8000-000000000512")
    await _store_media(async_session_factory, media_id, status)

    with pytest.raises(DraftArtifactsChangedError):
        await repository.enqueue(
            stored_draft,
            _payload(stored_draft, media_id),
            now=NOW,
            expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
        )


async def test_enqueue_accepts_an_exact_completed_media_snapshot(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    media_id = UUID("00000000-0000-4000-8000-000000000513")
    await _store_media(async_session_factory, media_id, MediaJobStatus.COMPLETED)

    result = await repository.enqueue(
        stored_draft,
        _payload(stored_draft, media_id),
        now=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )

    assert result.status is FinalizationJobStatus.PENDING


async def test_delete_and_enqueue_race_cannot_remove_a_processing_draft(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    finalizations = PostgresFinalizationJobRepository(async_session_factory)
    drafts = PostgresDraftRepository(async_session_factory)
    enqueue_result, delete_result = await asyncio.gather(
        finalizations.enqueue(
            stored_draft,
            _payload(stored_draft),
            now=NOW,
            expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
        ),
        drafts.delete_owned(DRAFT_ID, stored_draft.snapshot.owner_account_id),
        return_exceptions=True,
    )

    if isinstance(enqueue_result, BaseException):
        assert isinstance(enqueue_result, DraftNotFoundError)
        assert delete_result is True
        assert await finalizations.get(DRAFT_ID) is None
    else:
        assert enqueue_result.status is FinalizationJobStatus.PENDING
        assert isinstance(delete_result, DraftStateConflictError)
        assert await drafts.get(DRAFT_ID) is not None


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


async def test_status_translates_malformed_attention_entries_to_data_integrity_error(
    stored_draft: StoredDraft,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresFinalizationJobRepository(async_session_factory)
    await repository.record_preparation_attention(
        stored_draft,
        (SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING),),
        now=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
    )
    async with async_session_factory.begin() as session:
        await session.execute(
            update(SubmissionFinalizationJob)
            .where(SubmissionFinalizationJob.draft_id == DRAFT_ID)
            .values(attention_issues=[42])
        )

    with pytest.raises(DataIntegrityError, match="attention issues"):
        await repository.get(DRAFT_ID)


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
    assert draft.status is DraftStatus.NEEDS_ATTENTION
