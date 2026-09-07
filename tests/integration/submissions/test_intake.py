"""Supplied-file intent survives failed downloads and draft corrections."""

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import ConflictError
from squid.submissions.application.intake import IntakeStatus, SuppliedAttachment
from squid.submissions.infrastructure.intake import PostgresAttachmentIntake
from tests.integration.submissions.test_schematics import create_draft, reload
from tests.support.submission_targets import seed_account_and_version


async def test_interrupted_download_blocks_submission_until_explicit_resolution(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    intake = PostgresAttachmentIntake(sessions)
    source = SuppliedAttachment(uuid4(), "design.schem", "schematic", IntakeStatus.PENDING)
    await intake.reserve(draft, source)
    assert await intake.issues_for_draft(draft.snapshot.id)
    await intake.set_status(draft.snapshot.id, source.id, IntakeStatus.FAILED)
    assert await intake.issues_for_draft(draft.snapshot.id)
    await intake.set_status(draft.snapshot.id, source.id, IntakeStatus.DISCARDED)
    assert await intake.issues_for_draft(draft.snapshot.id) == ()
    await intake.set_status(draft.snapshot.id, source.id, IntakeStatus.READY)
    assert (await intake.list(draft.snapshot.id))[0].status is IntakeStatus.DISCARDED
    with pytest.raises(ConflictError):
        await intake.reserve(await reload(sessions, draft), source)


async def test_registration_resolves_intake_and_late_failure_does_not_downgrade_it(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    intake = PostgresAttachmentIntake(sessions)
    source = SuppliedAttachment(uuid4(), "image.png", "image", IntakeStatus.PENDING)
    await intake.reserve(draft, source)
    current = await reload(sessions, draft)
    assert current.snapshot.revision == draft.snapshot.revision + 1
    await intake.set_status(draft.snapshot.id, source.id, IntakeStatus.READY)
    await intake.set_status(draft.snapshot.id, source.id, IntakeStatus.FAILED)
    assert await intake.issues_for_draft(draft.snapshot.id) == ()
    await intake.reserve(current, source)
    assert (await reload(sessions, draft)).snapshot.revision == current.snapshot.revision
    with pytest.raises(ConflictError):
        await intake.reserve(current, SuppliedAttachment(source.id, "different.png", "image", IntakeStatus.PENDING))


async def test_source_manifest_blocks_even_if_process_dies_before_reserving_downloads(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from dataclasses import replace

    from squid.submissions.domain.source_files import SubmissionSourceFile
    from squid.submissions.infrastructure.repository import PostgresDraftRepository

    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    initial = await create_draft(sessions, owner)
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://cdn.discordapp.com/source.png", 4)
    draft = replace(initial, snapshot=replace(initial.snapshot, id=uuid4()), source_files=(source,))
    await PostgresDraftRepository(sessions).create(draft)
    intake = PostgresAttachmentIntake(sessions)
    assert await intake.issues_for_draft(draft.snapshot.id)
    pending = (await intake.list(draft.snapshot.id))[0]
    assert pending.status is IntakeStatus.PENDING
    assert pending.source == source
    await intake.reserve(draft, pending)
    await intake.set_status(draft.snapshot.id, source.id, IntakeStatus.DISCARDED)
    assert await intake.issues_for_draft(draft.snapshot.id) == ()
