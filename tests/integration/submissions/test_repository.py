"""PostgreSQL synchronized-draft repository integration tests."""

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.infrastructure.models import Account
from squid.media.application.jobs import MediaJobStatus
from squid.media.domain import MediaKind
from squid.media.infrastructure.models import MediaDraftReference, MediaNormalizationJobRecord, MediaUploadRecord
from squid.submissions.application import StoredDraft
from squid.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FinalizationJobStatus,
    SubmissionOrigin,
)
from squid.submissions.errors import DraftStateConflictError
from squid.submissions.infrastructure.finalization_models import SubmissionFinalizationJob
from squid.submissions.infrastructure.models import (
    SubmissionDraft,
    SubmissionDraftAccess,
    SubmissionDraftChange,
)
from squid.submissions.infrastructure.repository import PostgresDraftRepository

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000201")
NOW = Instant.parse_iso("2026-08-11T12:00:00Z")
_TABLES = (
    cast(Table, Account.__table__),
    cast(Table, SubmissionDraft.__table__),
    cast(Table, SubmissionDraftAccess.__table__),
    cast(Table, SubmissionDraftChange.__table__),
    cast(Table, SubmissionFinalizationJob.__table__),
    cast(Table, MediaUploadRecord.__table__),
    cast(Table, MediaNormalizationJobRecord.__table__),
    cast(Table, MediaDraftReference.__table__),
)


@pytest.fixture
async def draft_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        for table in _TABLES:
            await connection.run_sync(table.create)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            for table in reversed(_TABLES):
                await connection.run_sync(table.drop)


@pytest.fixture
async def account_id(
    draft_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> int:
    async with async_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        return account.id


def _stored(account_id: int, *, origin: SubmissionOrigin = SubmissionOrigin.WEB) -> StoredDraft:
    return StoredDraft(
        snapshot=DraftSnapshot(
            id=DRAFT_ID,
            owner_account_id=account_id,
            schema_id="build_submission.v1",
            schema_revision=1,
            category="other",
        ),
        origin=origin,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW.add(days=7, days_assumed_24h_ok=True),
        source_installation_id=(
            UUID("00000000-0000-4000-8000-000000000299") if origin is SubmissionOrigin.PAPER else None
        ),
    )


def _change(*, key: str, operation_id: str) -> DraftChange:
    return DraftChange(
        base_revision=0,
        client_instance_id="web:integration-test",
        idempotency_key=DraftChangeKey(key),
        operations=(
            FieldOperation(
                UUID(operation_id),
                "display_name",
                FieldOperationKind.SET,
                key,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_create_adds_owner_grant_and_replays_change(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    created = await repository.create(_stored(account_id))
    change = _change(key="operation-a", operation_id="00000000-0000-4000-8000-000000000202")

    applied = await repository.apply_change(
        DRAFT_ID,
        account_id,
        change,
        updated_at=NOW.add(seconds=1),
        expires_at=NOW.add(days=7, seconds=1, days_assumed_24h_ok=True),
    )
    replayed = await repository.apply_change(
        DRAFT_ID,
        account_id,
        change,
        updated_at=NOW.add(seconds=2),
        expires_at=NOW.add(days=7, seconds=2, days_assumed_24h_ok=True),
    )

    assert created.snapshot.revision == 0
    assert applied.draft.snapshot.revision == 1
    assert replayed.replayed is True
    assert replayed.draft.snapshot.revision == 1
    async with async_session_factory() as session:
        grants = await session.scalar(select(func.count()).select_from(SubmissionDraftAccess))
        changes = await session.scalar(select(func.count()).select_from(SubmissionDraftChange))
    assert grants == 1
    assert changes == 1


@pytest.mark.asyncio
async def test_paper_draft_round_trip_retains_server_derived_installation(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)

    created = await repository.create(_stored(account_id, origin=SubmissionOrigin.PAPER))
    loaded = await repository.get(created.snapshot.id)

    assert loaded is not None
    assert loaded.origin is SubmissionOrigin.PAPER
    assert loaded.source_installation_id == UUID("00000000-0000-4000-8000-000000000299")


@pytest.mark.asyncio
async def test_manifest_upgrade_is_optimistic_and_idempotent_by_target(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    await repository.create(_stored(account_id))

    upgraded = await repository.upgrade_manifest(
        DRAFT_ID,
        account_id,
        expected_revision=0,
        target_schema_revision=2,
        answers={"completion": "Built at spawn"},
        updated_at=NOW.add(seconds=1),
        expires_at=NOW.add(days=7, seconds=1, days_assumed_24h_ok=True),
    )
    replayed = await repository.upgrade_manifest(
        DRAFT_ID,
        account_id,
        expected_revision=0,
        target_schema_revision=2,
        answers={"completion": "ignored replay payload"},
        updated_at=NOW.add(seconds=2),
        expires_at=NOW.add(days=7, seconds=2, days_assumed_24h_ok=True),
    )

    assert upgraded.draft.snapshot.schema_revision == 2
    assert upgraded.draft.snapshot.revision == 1
    assert upgraded.draft.snapshot.answers == {"completion": "Built at spawn"}
    assert replayed.replayed
    assert replayed.draft == upgraded.draft


@pytest.mark.asyncio
async def test_active_draft_discovery_is_newest_first_and_excludes_inactive_rows(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    first = replace(
        _stored(account_id),
        snapshot=replace(_stored(account_id).snapshot, id=UUID("00000000-0000-4000-8000-000000000210")),
    )
    newest = replace(
        first,
        snapshot=replace(first.snapshot, id=UUID("00000000-0000-4000-8000-000000000211")),
        updated_at=NOW.add(seconds=2),
    )
    expired = replace(
        first,
        snapshot=replace(first.snapshot, id=UUID("00000000-0000-4000-8000-000000000212")),
        expires_at=NOW.add(seconds=1),
    )
    submitted = replace(
        first,
        snapshot=replace(
            first.snapshot,
            id=UUID("00000000-0000-4000-8000-000000000213"),
            status=DraftStatus.SUBMITTED,
        ),
    )
    for draft in (first, newest, expired, submitted):
        await repository.create(draft)

    discovery_time = NOW.add(seconds=2)
    discovered = await repository.list_active_for_account(account_id, now=discovery_time, limit=10)

    assert tuple(draft.snapshot.id for draft in discovered) == (newest.snapshot.id, first.snapshot.id)
    assert await repository.list_active_for_account(account_id, now=discovery_time, limit=1) == (newest,)


@pytest.mark.asyncio
async def test_concurrent_same_revision_changes_have_one_winner(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    await repository.create(_stored(account_id))
    changes = (
        _change(key="operation-a", operation_id="00000000-0000-4000-8000-000000000203"),
        _change(key="operation-b", operation_id="00000000-0000-4000-8000-000000000204"),
    )

    results = await asyncio.gather(
        *(
            repository.apply_change(
                DRAFT_ID,
                account_id,
                change,
                updated_at=NOW.add(seconds=index),
                expires_at=NOW.add(days=7, seconds=index, days_assumed_24h_ok=True),
            )
            for index, change in enumerate(changes, start=1)
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, DraftRevisionConflictError) for result in results) == 1


@pytest.mark.asyncio
async def test_expired_drafts_stop_consuming_capacity_and_can_be_marked_expired(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    stored = _stored(account_id)
    stored = StoredDraft(
        snapshot=stored.snapshot,
        origin=stored.origin,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        expires_at=NOW.add(seconds=1),
    )
    await repository.create(stored)

    assert await repository.expire_due(now=NOW) == 0
    assert await repository.expire_due(now=NOW.add(seconds=2)) == 1
    assert await repository.count_active_for_account(account_id) == 0


@pytest.mark.asyncio
async def test_expiry_fences_finalization_and_discards_media(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    stored = _stored(account_id)
    stored = replace(
        stored,
        snapshot=replace(stored.snapshot, status=DraftStatus.NEEDS_ATTENTION),
        expires_at=NOW.add(seconds=1),
    )
    await repository.create(stored)
    upload_id = UUID("00000000-0000-4000-8000-000000000205")
    async with async_session_factory.begin() as session:
        for attempt_number in (1, 2):
            session.add(
                SubmissionFinalizationJob(
                    draft_id=DRAFT_ID,
                    attempt_number=attempt_number,
                    draft_revision=0,
                    payload=None,
                    payload_sha256=None,
                    status=FinalizationJobStatus.NEEDS_ATTENTION,
                    available_at=NOW,
                    attention_at=NOW,
                    attention_issues=[{"field_id": "schematic", "reason": "schematic_required"}],
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        session.add(
            MediaUploadRecord(
                id=upload_id,
                draft_id=DRAFT_ID,
                kind=MediaKind.IMAGE,
                source_content_type="image/png",
                source_byte_size=3,
                source_sha256="a" * 64,
                source_object_key=f"media/raw/{upload_id}",
                strip_audio=False,
                created_at=NOW,
            )
        )
        await session.flush()
        session.add(MediaNormalizationJobRecord(upload_id=upload_id, status=MediaJobStatus.PENDING, available_at=NOW))

    assert await repository.expire_due(now=NOW.add(seconds=2)) == 1
    async with async_session_factory() as session:
        draft = await session.get(SubmissionDraft, DRAFT_ID)
        finalization = await session.scalar(
            select(SubmissionFinalizationJob).where(SubmissionFinalizationJob.draft_id == DRAFT_ID)
        )
        media = await session.get(MediaNormalizationJobRecord, upload_id)

    assert draft is not None
    assert draft.status is DraftStatus.EXPIRED
    assert finalization is None
    assert media is not None
    assert media.status == "discarded"
    assert media.discarded_at == NOW.add(seconds=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [DraftStatus.PROCESSING, DraftStatus.SUBMITTED, DraftStatus.EXPIRED])
async def test_delete_owned_rechecks_noneditable_status_under_repository_lock(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
    status: DraftStatus,
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    await repository.create(_stored(account_id))
    async with async_session_factory.begin() as session:
        draft = await session.get(SubmissionDraft, DRAFT_ID)
        assert draft is not None
        draft.status = status

    with pytest.raises(DraftStateConflictError) as exc_info:
        await repository.delete_owned(DRAFT_ID, account_id)

    assert exc_info.value.public_context == {"status": status.value, "operation": "delete"}
    assert await repository.get(DRAFT_ID) is not None


@pytest.mark.asyncio
async def test_delete_owned_accepts_needs_attention(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = PostgresDraftRepository(async_session_factory)
    await repository.create(_stored(account_id))
    async with async_session_factory.begin() as session:
        draft = await session.get(SubmissionDraft, DRAFT_ID)
        assert draft is not None
        draft.status = DraftStatus.NEEDS_ATTENTION

    assert await repository.delete_owned(DRAFT_ID, account_id) is True
    assert await repository.get(DRAFT_ID) is None


@pytest.mark.parametrize("inferred", [False, True])
async def test_concurrent_creation_respects_capacity_and_replays_stable_sources(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
    inferred: bool,
) -> None:
    from anyio import create_task_group

    from squid.submissions.errors import DraftCapacityExceededError

    repository = PostgresDraftRepository(async_session_factory)
    now = Instant.now()
    template = replace(
        _stored(account_id, origin=SubmissionOrigin.DISCORD),
        inferred=inferred,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    accepted: list[StoredDraft] = []
    rejected: list[DraftCapacityExceededError] = []

    async def create(identifier: int) -> None:
        draft = replace(template, snapshot=replace(template.snapshot, id=UUID(int=identifier)))
        try:
            accepted.append(await repository.create(draft, capacity=1))
        except DraftCapacityExceededError as error:
            rejected.append(error)

    async with create_task_group() as tasks:
        tasks.start_soon(create, 800)
        tasks.start_soon(create, 801)
    assert len(accepted) == 1
    assert len(rejected) == 1
    replayed = await repository.create(accepted[0], capacity=1)
    assert replayed.snapshot.id == accepted[0].snapshot.id

    other_pool = replace(template, inferred=not inferred, snapshot=replace(template.snapshot, id=UUID(int=802)))
    assert (await repository.create(other_pool, capacity=1)).inferred is not inferred


async def test_inferred_capacity_is_global_and_expiry_releases_it(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from squid.submissions.errors import DraftCapacityExceededError

    repository = PostgresDraftRepository(async_session_factory)
    async with async_session_factory.begin() as session:
        other = Account()
        session.add(other)
        await session.flush()
        other_id = other.id
    now = Instant.now()
    first = replace(
        _stored(account_id, origin=SubmissionOrigin.DISCORD),
        inferred=True,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    await repository.create(first, capacity=1)
    second = replace(first, snapshot=replace(first.snapshot, id=UUID(int=803), owner_account_id=other_id))
    with pytest.raises(DraftCapacityExceededError):
        await repository.create(second, capacity=1)
    await repository.expire_due(now=first.expires_at)
    assert (await repository.create(second, capacity=1)).snapshot.owner_account_id == other_id


async def test_staff_edit_retains_owner_and_records_actual_actor(
    account_id: int,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        staff = Account()
        session.add(staff)
        await session.flush()
        staff_id = staff.id
    drafts = PostgresDraftRepository(async_session_factory)
    stored = replace(
        _stored(account_id), snapshot=replace(_stored(account_id).snapshot, status=DraftStatus.NEEDS_ATTENTION)
    )
    await drafts.create(stored)
    assert [draft.snapshot.id for draft in await drafts.list_attention(after=None, limit=1, now=NOW)] == [DRAFT_ID]
    assert await drafts.list_attention(after=DRAFT_ID, limit=1, now=NOW) == ()
    changed = await drafts.apply_change(
        DRAFT_ID,
        account_id,
        _change(key="staff-correction", operation_id="00000000-0000-4000-8000-000000000299"),
        updated_at=NOW,
        expires_at=stored.expires_at,
        actor_account_id=staff_id,
    )
    assert changed.draft.snapshot.owner_account_id == account_id
    async with async_session_factory() as session:
        assert await session.scalar(select(SubmissionDraftChange.actor_account_id)) == staff_id
    assert await drafts.list_attention(after=None, limit=1, now=NOW) == ()
