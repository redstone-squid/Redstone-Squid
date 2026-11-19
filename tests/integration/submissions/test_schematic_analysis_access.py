"""Stored schematic evidence is scoped to a retained draft reference."""

from functools import partial
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.schematics.application import IngestedSchematic
from squid.core.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from tests.integration.submissions.test_schematics import create_draft, reload
from tests.support.submission_targets import seed_account_and_version
from tests.support.unit_of_work import transact
from tests.unit.schematics.fakes import make_analysis


async def test_only_ready_retained_references_can_read_analysis(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    uows = partial(SqlAlchemyUnitOfWork, sessions)
    owner = await seed_account_and_version(sessions)
    draft = await create_draft(sessions, owner)
    source = DraftSchematic(uuid4(), "door.schem", "a" * 64, 3, DraftSchematicState.UPLOADING)
    await transact(uows, lambda uow: uow.draft_schematics.register(draft, source, owner), commit=True)
    assert (
        await transact(uows, lambda uow: uow.draft_schematics.analysis_for_draft(draft.snapshot.id, source.id)) is None
    )
    await transact(uows, lambda uow: uow.draft_schematics.finish_upload(source.id, succeeded=True), commit=True)
    analysis = make_analysis(sha256="a" * 64)
    await transact(
        uows,
        lambda uow: uow.draft_schematics.finish_analysis(source, IngestedSchematic("a" * 64, analysis)),
        commit=True,
    )
    assert (
        await transact(uows, lambda uow: uow.draft_schematics.analysis_for_draft(draft.snapshot.id, source.id))
        == analysis
    )
    assert await transact(uows, lambda uow: uow.draft_schematics.analysis_for_draft(uuid4(), source.id)) is None
    current = await reload(sessions, draft)
    await transact(uows, lambda uow: uow.draft_schematics.discard(current, source.id), commit=True)
    assert (
        await transact(uows, lambda uow: uow.draft_schematics.analysis_for_draft(draft.snapshot.id, source.id)) is None
    )
