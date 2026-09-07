"""Private schematic retention, selection, and shared-reference cleanup."""

from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.errors import ConflictError
from squid.submissions.application import StoredDraft
from squid.submissions.domain import DraftSnapshot, SubmissionOrigin
from squid.submissions.domain.finalization import SchematicArtifactState, SubmissionAttentionReason
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.submissions.infrastructure.repository import PostgresDraftRepository
from squid.submissions.infrastructure.schematic_models import DraftSchematicSource
from squid.submissions.infrastructure.schematics import PostgresDraftSchematics
from tests.support.submission_targets import seed_account_and_version


async def create_draft(sessions: async_sessionmaker[AsyncSession], owner: int) -> StoredDraft:
    now = Instant.now()
    draft = StoredDraft(
        snapshot=DraftSnapshot(uuid4(), owner, "build_submission.v1", 1, "other"),
        origin=SubmissionOrigin.WEB,
        created_at=now,
        updated_at=now,
        expires_at=now.add(days=7, days_assumed_24h_ok=True),
    )
    return await PostgresDraftRepository(sessions).create(draft)


async def reload(sessions: async_sessionmaker[AsyncSession], draft: StoredDraft) -> StoredDraft:
    current = await PostgresDraftRepository(sessions).get(draft.snapshot.id)
    assert current is not None
    return current


async def test_schematics_require_explicit_primary_and_never_bypass_sanitizer(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    repo = PostgresDraftSchematics(sessions)
    first = DraftSchematic(uuid4(), "first.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    second = DraftSchematic(uuid4(), "second.schem", "b" * 64, 4, DraftSchematicState.UPLOADING)
    assert (await repo.register(draft, first, owner)).primary
    await repo.finish_upload(first.id, succeeded=True)
    await repo.register(await reload(sessions, draft), second, owner)
    await repo.finish_upload(second.id, succeeded=True)
    state = await repo.read_for_draft(draft.snapshot.id)
    assert state.state is SchematicArtifactState.PROCESSING
    assert state.issues[0].reason is SubmissionAttentionReason.SCHEMATIC_PRIMARY_REQUIRED
    await repo.select_primary(await reload(sessions, draft), second.id)
    state = await repo.read_for_draft(draft.snapshot.id)
    assert state.state is SchematicArtifactState.PROCESSING
    assert state.sanitized is None
    assert state.issues == ()
    await repo.discard(await reload(sessions, draft), second.id)
    assert next(item for item in await repo.list(draft.snapshot.id) if item.id == first.id).primary


async def test_failed_reserved_download_can_retry_but_cannot_change_known_bytes(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    repo = PostgresDraftSchematics(sessions)
    source_id = uuid4()
    await repo.register(
        draft, DraftSchematic(source_id, "source.schem", None, None, DraftSchematicState.UPLOADING), owner
    )
    await repo.finish_upload(source_id, succeeded=False)
    assert (await repo.read_for_draft(draft.snapshot.id)).state is SchematicArtifactState.REJECTED
    source = DraftSchematic(source_id, "source.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    await repo.register(await reload(sessions, draft), source, owner)
    await repo.finish_upload(source_id, succeeded=True)
    await repo.finish_upload(source_id, succeeded=False)
    assert (await repo.list(draft.snapshot.id))[0].state is DraftSchematicState.WAITING_SANITIZER
    different = DraftSchematic(source_id, "source.schem", "b" * 64, 3, DraftSchematicState.UPLOADING)
    with pytest.raises(ConflictError):
        await repo.register(await reload(sessions, draft), different, owner)


async def test_cleanup_preserves_shared_source_until_every_reference_is_discarded(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    first = await create_draft(sessions, owner)
    second = await create_draft(sessions, owner)
    repo = PostgresDraftSchematics(sessions)
    source = DraftSchematic(uuid4(), "source.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    await repo.register(first, source, owner)
    await repo.finish_upload(source.id, succeeded=True)
    await repo.attach(await reload(sessions, first), second, source.id)
    await repo.discard(await reload(sessions, first), source.id)
    async with sessions.begin() as session:
        await session.execute(update(DraftSchematicSource).values(updated_at=Instant.now().subtract(minutes=10)))
    assert await repo.cleanup_candidates(limit=20) == ()
    await repo.discard(await reload(sessions, second), source.id)
    assert await repo.cleanup_candidates(limit=20) == (source.id,)
    # A crashed storage deletion is retried with the same durable identity.
    assert await repo.cleanup_candidates(limit=20) == (source.id,)
    await repo.mark_deleted(source.id)
    assert await repo.cleanup_candidates(limit=20) == ()
    assert (await repo.read_for_draft(first.snapshot.id)).state is SchematicArtifactState.ABSENT
