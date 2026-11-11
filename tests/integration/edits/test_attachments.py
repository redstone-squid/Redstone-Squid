"""Attachment inverses keep identities and processing results until their leases expire."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import ConflictError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, Revision, RevisionConflictError
from squid.edits.models import EditResourceLease
from squid.media.application.jobs import MediaUploadMetadata
from squid.media.domain import MediaKind, MediaLimits
from squid.media.domain.jobs import MediaJobStatus
from squid.media.models import MediaNormalizationJobRecord
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.schematics.application import IngestedSchematic
from squid.submissions.application.attachment_edits import (
    DRAFT_ATTACHMENTS,
    AttachmentValues,
    DraftAttachmentsEdit,
    FileMembership,
)
from squid.submissions.application.drafts import SubmissionDraftService
from squid.submissions.application.edits import draft_target
from squid.submissions.application.forms import CheckedInFormManifestRegistry
from squid.submissions.application.intake import SuppliedAttachment
from squid.submissions.domain.intake import IntakeStatus
from squid.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.submissions.models.schematics import DraftSchematicSource
from tests.integration.submissions.test_schematics import create_draft, reload
from tests.support.submission_targets import seed_account_and_version
from tests.unit.schematics.fakes import make_analysis


async def test_schematic_removal_restores_analysis_and_primary_and_expires_safely(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    actor = Actor(account_id=owner)
    draft = await create_draft(sessions, owner)
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    service = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=factory)
    executor = EditExecutor(factory, {DRAFT_ATTACHMENTS: DraftAttachmentsEdit(service)})
    first = DraftSchematic(uuid4(), "first.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    second = DraftSchematic(uuid4(), "second.schem", "b" * 64, 3, DraftSchematicState.UPLOADING)
    for source in (first, second):
        draft = await reload(sessions, draft)
        async with factory() as uow:
            await uow.draft_schematics.register(draft, source, owner)
            await uow.draft_schematics.finish_upload(source.id, succeeded=True)
            assert source.sha256 is not None
            await uow.draft_schematics.finish_analysis(
                source, IngestedSchematic(source.sha256, make_analysis(sha256=source.sha256))
            )
            await uow.commit()
    draft = await reload(sessions, draft)
    selected = await executor.execute(
        EditCommand(
            DRAFT_ATTACHMENTS,
            (Revision(draft_target(draft.snapshot.id), draft.snapshot.revision),),
            AttachmentValues(primary=first.id, select_primary=True).edit_values(),
        ),
        actor,
    )
    removed = await executor.execute(
        EditCommand(
            DRAFT_ATTACHMENTS,
            selected.outcome.revisions,
            AttachmentValues(files=(FileMembership(source_id=first.id, present=False),)).edit_values(),
        ),
        actor,
    )
    assert removed.receipt is not None
    async with factory() as uow:
        files = await uow.draft_schematics.list(draft.snapshot.id)
        assert next(item for item in files if item.id == first.id).discarded
        assert next(item for item in files if item.id == second.id).primary
    async with sessions.begin() as session:
        await session.execute(update(DraftSchematicSource).values(updated_at=Instant.now().subtract(hours=1)))
    async with factory() as uow:
        assert await uow.draft_schematics.cleanup_candidates(limit=10) == ()
    undone = await executor.execute(removed.receipt.inverse(removed.outcome.revisions), actor)
    assert undone.receipt is not None
    async with factory() as uow:
        restored = next(item for item in await uow.draft_schematics.list(draft.snapshot.id) if item.id == first.id)
        assert restored.state is DraftSchematicState.READY
        assert restored.primary
        assert not restored.discarded
    with pytest.raises(RevisionConflictError):
        await executor.execute(removed.receipt.inverse(removed.outcome.revisions), actor)
    redone = await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)
    assert redone.receipt is not None
    async with sessions.begin() as session:
        await session.execute(update(EditResourceLease).values(expires_at=Instant.now().subtract(seconds=1)))
    async with factory() as uow:
        assert await uow.draft_schematics.cleanup_candidates(limit=10) == (first.id,)
        await uow.commit()
    with pytest.raises(ConflictError):
        await executor.execute(redone.receipt.inverse(redone.outcome.revisions), actor)


async def test_media_removal_preserves_job_and_hidden_intake_completion(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    actor = Actor(account_id=owner)
    draft = await create_draft(sessions, owner)
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    service = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=factory)
    executor = EditExecutor(factory, {DRAFT_ATTACHMENTS: DraftAttachmentsEdit(service)})
    source = uuid4()
    async with factory() as uow:
        await uow.attachment_intake.reserve(
            draft, SuppliedAttachment(source, "image.png", "image", IntakeStatus.PENDING)
        )
        await uow.commit()
    draft = await reload(sessions, draft)
    async with factory() as uow:
        await uow.media_jobs.enqueue(
            MediaUploadMetadata(
                source, draft.snapshot.id, MediaKind.IMAGE, "image/png", 10, "a" * 64, "raw/image", strip_audio=False
            ),
            MediaLimits(),
        )
        await uow.commit()
    removed = await executor.execute(
        EditCommand(
            DRAFT_ATTACHMENTS,
            (Revision(draft_target(draft.snapshot.id), draft.snapshot.revision),),
            AttachmentValues(files=(FileMembership(source_id=source, present=False),)).edit_values(),
        ),
        actor,
    )
    assert removed.receipt is not None
    async with factory() as uow:
        await uow.attachment_intake.set_status(draft.snapshot.id, source, IntakeStatus.READY)
        assert await uow.media_jobs.terminal_sources(limit=10) == ()
        await uow.commit()
    undone = await executor.execute(removed.receipt.inverse(removed.outcome.revisions), actor)
    assert undone.receipt is not None
    async with factory() as uow:
        assert (await uow.attachment_intake.list(draft.snapshot.id))[0].status is IntakeStatus.READY
        assert (await uow.media_jobs.list_for_draft(draft.snapshot.id))[0].status is MediaJobStatus.PENDING
    redone = await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)
    assert redone.receipt is not None
    async with sessions.begin() as session:
        await session.execute(update(EditResourceLease).values(expires_at=Instant.now().subtract(seconds=1)))
    async with factory() as uow:
        assert len(await uow.media_jobs.terminal_sources(limit=10)) == 1
        await uow.commit()
    async with sessions() as session:
        assert (
            await session.scalar(
                select(MediaNormalizationJobRecord.status).where(MediaNormalizationJobRecord.upload_id == source)
            )
            is MediaJobStatus.DISCARDED
        )
    with pytest.raises(ConflictError):
        await executor.execute(redone.receipt.inverse(redone.outcome.revisions), actor)
