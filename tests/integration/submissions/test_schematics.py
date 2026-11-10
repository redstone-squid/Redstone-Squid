"""Private schematic retention, selection, and shared-reference cleanup."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import ConflictError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.application import StoredDraft
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.domain.finalization import SchematicArtifactState, SubmissionAttentionReason
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.submissions.models.schematics import DraftSchematicSource
from tests.support.submission_targets import seed_account_and_version
from tests.support.unit_of_work import transact


async def create_draft(sessions: async_sessionmaker[AsyncSession], owner: int) -> StoredDraft:
    now = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(uuid4(), owner, "build_submission.v1", 1, "other"),
        origin=SubmissionOrigin.WEB,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    return await transact(
        partial(SqlAlchemyUnitOfWork, sessions), lambda uow: uow.submission_drafts.create(draft), commit=True
    )


async def reload(sessions: async_sessionmaker[AsyncSession], draft: StoredDraft) -> StoredDraft:
    current = await transact(
        partial(SqlAlchemyUnitOfWork, sessions), lambda uow: uow.submission_drafts.get(draft.snapshot.id)
    )
    assert current is not None
    return current


async def test_schematics_require_explicit_primary_and_never_bypass_sanitizer(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    repo = partial(SqlAlchemyUnitOfWork, sessions)
    first = DraftSchematic(uuid4(), "first.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    second = DraftSchematic(uuid4(), "second.schem", "b" * 64, 4, DraftSchematicState.UPLOADING)
    assert (await transact(repo, lambda uow: uow.draft_schematics.register(draft, first, owner), commit=True)).primary
    await transact(repo, lambda uow: uow.draft_schematics.finish_upload(first.id, succeeded=True), commit=True)
    current = await reload(sessions, draft)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.register(current, second, owner),
        commit=True,
    )
    await transact(repo, lambda uow: uow.draft_schematics.finish_upload(second.id, succeeded=True), commit=True)
    state = await transact(repo, lambda uow: uow.draft_schematics.read_for_draft(draft.snapshot.id))
    assert state.state is SchematicArtifactState.PROCESSING
    assert state.issues[0].reason is SubmissionAttentionReason.SCHEMATIC_PRIMARY_REQUIRED
    current = await reload(sessions, draft)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.select_primary(current, second.id),
        commit=True,
    )
    state = await transact(repo, lambda uow: uow.draft_schematics.read_for_draft(draft.snapshot.id))
    assert state.state is SchematicArtifactState.PROCESSING
    assert state.artifact_id is None
    assert state.issues == ()
    current = await reload(sessions, draft)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.discard(current, second.id),
        commit=True,
    )
    assert next(
        item
        for item in await transact(repo, lambda uow: uow.draft_schematics.list(draft.snapshot.id))
        if item.id == first.id
    ).primary


async def test_failed_reserved_download_can_retry_but_cannot_change_known_bytes(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    repo = partial(SqlAlchemyUnitOfWork, sessions)
    source_id = uuid4()
    await transact(
        repo,
        lambda uow: uow.draft_schematics.register(
            draft, DraftSchematic(source_id, "source.schem", None, None, DraftSchematicState.UPLOADING), owner
        ),
        commit=True,
    )
    await transact(repo, lambda uow: uow.draft_schematics.finish_upload(source_id, succeeded=False), commit=True)
    assert (
        await transact(repo, lambda uow: uow.draft_schematics.read_for_draft(draft.snapshot.id))
    ).state is SchematicArtifactState.REJECTED
    source = DraftSchematic(source_id, "source.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    current = await reload(sessions, draft)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.register(current, source, owner),
        commit=True,
    )
    await transact(repo, lambda uow: uow.draft_schematics.finish_upload(source_id, succeeded=True), commit=True)
    await transact(repo, lambda uow: uow.draft_schematics.finish_upload(source_id, succeeded=False), commit=True)
    assert (await transact(repo, lambda uow: uow.draft_schematics.list(draft.snapshot.id)))[
        0
    ].state is DraftSchematicState.PENDING
    different = DraftSchematic(source_id, "source.schem", "b" * 64, 3, DraftSchematicState.UPLOADING)
    current = await reload(sessions, draft)
    with pytest.raises(ConflictError):
        await transact(
            repo,
            lambda uow: uow.draft_schematics.register(current, different, owner),
            commit=True,
        )


async def test_cleanup_preserves_shared_source_until_every_reference_is_discarded(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    first = await create_draft(sessions, owner)
    second = await create_draft(sessions, owner)
    repo = partial(SqlAlchemyUnitOfWork, sessions)
    source = DraftSchematic(uuid4(), "source.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    await transact(repo, lambda uow: uow.draft_schematics.register(first, source, owner), commit=True)
    await transact(repo, lambda uow: uow.draft_schematics.finish_upload(source.id, succeeded=True), commit=True)
    current = await reload(sessions, first)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.attach(current, second, source.id),
        commit=True,
    )
    current = await reload(sessions, first)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.discard(current, source.id),
        commit=True,
    )
    async with sessions.begin() as session:
        await session.execute(update(DraftSchematicSource).values(updated_at=Instant.now().subtract(minutes=10)))
    assert await transact(repo, lambda uow: uow.draft_schematics.cleanup_candidates(limit=20), commit=True) == ()
    current = await reload(sessions, second)
    await transact(
        repo,
        lambda uow: uow.draft_schematics.discard(current, source.id),
        commit=True,
    )
    assert await transact(repo, lambda uow: uow.draft_schematics.cleanup_candidates(limit=20), commit=True) == (
        source.id,
    )
    # A crashed storage deletion is retried with the same durable identity.
    assert await transact(repo, lambda uow: uow.draft_schematics.cleanup_candidates(limit=20), commit=True) == (
        source.id,
    )
    await transact(repo, lambda uow: uow.draft_schematics.mark_deleted(source.id), commit=True)
    assert await transact(repo, lambda uow: uow.draft_schematics.cleanup_candidates(limit=20), commit=True) == ()
    assert (
        await transact(repo, lambda uow: uow.draft_schematics.read_for_draft(first.snapshot.id))
    ).state is SchematicArtifactState.ABSENT
