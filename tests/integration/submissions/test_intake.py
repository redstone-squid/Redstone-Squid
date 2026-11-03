"""Supplied-file intent survives failed downloads and draft corrections."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import ConflictError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.application.intake import SuppliedAttachment
from squid.submissions.domain.intake import IntakeStatus
from tests.integration.submissions.test_schematics import create_draft, reload
from tests.support.submission_targets import seed_account_and_version
from tests.support.unit_of_work import transact


async def test_interrupted_download_blocks_submission_until_explicit_resolution(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    intake = partial(SqlAlchemyUnitOfWork, sessions)
    source = SuppliedAttachment(uuid4(), "design.schem", "schematic", IntakeStatus.PENDING)
    await transact(intake, lambda uow: uow.attachment_intake.reserve(draft, source), commit=True)
    assert await transact(intake, lambda uow: uow.attachment_intake.issues_for_draft(draft.snapshot.id))
    await transact(
        intake,
        lambda uow: uow.attachment_intake.set_status(draft.snapshot.id, source.id, IntakeStatus.FAILED),
        commit=True,
    )
    assert await transact(intake, lambda uow: uow.attachment_intake.issues_for_draft(draft.snapshot.id))
    await transact(
        intake,
        lambda uow: uow.attachment_intake.set_status(draft.snapshot.id, source.id, IntakeStatus.DISCARDED),
        commit=True,
    )
    assert await transact(intake, lambda uow: uow.attachment_intake.issues_for_draft(draft.snapshot.id)) == ()
    await transact(
        intake,
        lambda uow: uow.attachment_intake.set_status(draft.snapshot.id, source.id, IntakeStatus.READY),
        commit=True,
    )
    assert (await transact(intake, lambda uow: uow.attachment_intake.list(draft.snapshot.id)))[
        0
    ].status is IntakeStatus.DISCARDED
    current = await reload(sessions, draft)
    with pytest.raises(ConflictError):
        await transact(
            intake,
            lambda uow: uow.attachment_intake.reserve(current, source),
            commit=True,
        )


async def test_registration_resolves_intake_and_late_failure_does_not_downgrade_it(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    intake = partial(SqlAlchemyUnitOfWork, sessions)
    source = SuppliedAttachment(uuid4(), "image.png", "image", IntakeStatus.PENDING)
    await transact(intake, lambda uow: uow.attachment_intake.reserve(draft, source), commit=True)
    current = await reload(sessions, draft)
    assert current.snapshot.revision == draft.snapshot.revision + 1
    await transact(
        intake,
        lambda uow: uow.attachment_intake.set_status(draft.snapshot.id, source.id, IntakeStatus.READY),
        commit=True,
    )
    await transact(
        intake,
        lambda uow: uow.attachment_intake.set_status(draft.snapshot.id, source.id, IntakeStatus.FAILED),
        commit=True,
    )
    assert await transact(intake, lambda uow: uow.attachment_intake.issues_for_draft(draft.snapshot.id)) == ()
    await transact(intake, lambda uow: uow.attachment_intake.reserve(current, source), commit=True)
    assert (await reload(sessions, draft)).snapshot.revision == current.snapshot.revision
    with pytest.raises(ConflictError):
        await transact(
            intake,
            lambda uow: uow.attachment_intake.reserve(
                current, SuppliedAttachment(source.id, "different.png", "image", IntakeStatus.PENDING)
            ),
            commit=True,
        )


async def test_source_manifest_blocks_even_if_process_dies_before_reserving_downloads(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from dataclasses import replace

    from squid.submissions.domain.source_files import SubmissionSourceFile

    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    initial = await create_draft(sessions, owner)
    source = SubmissionSourceFile(uuid4(), "image.png", "image/png", "https://cdn.discordapp.com/source.png", 4)
    draft = replace(initial, snapshot=replace(initial.snapshot, id=uuid4()), source_files=(source,))
    await transact(
        partial(SqlAlchemyUnitOfWork, sessions), lambda uow: uow.submission_drafts.create(draft), commit=True
    )
    intake = partial(SqlAlchemyUnitOfWork, sessions)
    assert await transact(intake, lambda uow: uow.attachment_intake.issues_for_draft(draft.snapshot.id))
    pending = (await transact(intake, lambda uow: uow.attachment_intake.list(draft.snapshot.id)))[0]
    assert pending.status is IntakeStatus.PENDING
    assert pending.source == source
    await transact(intake, lambda uow: uow.attachment_intake.reserve(draft, pending), commit=True)
    await transact(
        intake,
        lambda uow: uow.attachment_intake.set_status(draft.snapshot.id, source.id, IntakeStatus.DISCARDED),
        commit=True,
    )
    assert await transact(intake, lambda uow: uow.attachment_intake.issues_for_draft(draft.snapshot.id)) == ()
