"""Draft history shares revision checks, audit changes, and atomic operation outcomes."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.models import Account
from squid.core.actors import Actor
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, Revision, RevisionConflictError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.submissions.application.edits import DRAFT_ANSWERS, DraftAnswersEdit, answer_values, draft_target
from squid.submissions.application.forms import (
    CURRENT_SUBMISSION_SCHEMA,
    CURRENT_SUBMISSION_SCHEMA_REVISION,
    CheckedInFormManifestRegistry,
)
from squid.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftSnapshot,
    FieldOperation,
    FieldOperationKind,
    SubmissionOrigin,
)
from squid.submissions.models.entities import SubmissionDraftChange


async def test_answers_undo_replay_and_external_change_conflict(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        actor = Actor(account_id=account.id)
    now = Instant.now()
    draft = StoredDraft(
        DraftSnapshot(uuid4(), account.id, CURRENT_SUBMISSION_SCHEMA, CURRENT_SUBMISSION_SCHEMA_REVISION, "other"),
        SubmissionOrigin.DISCORD,
        now,
        now,
        now.add(hours=24),
    )
    async with factory() as uow:
        await uow.submission_drafts.create(draft)
        await uow.commit()
    service = SubmissionDraftService(CheckedInFormManifestRegistry(), uow_factory=factory)
    executor = EditExecutor(factory, {DRAFT_ANSWERS: DraftAnswersEdit(service)})
    edit = EditCommand(
        DRAFT_ANSWERS,
        (Revision(draft_target(draft.snapshot.id), draft.snapshot.revision),),
        answer_values({"display_name": "First"}),
    )
    saved = await executor.execute(edit, actor)
    assert saved.receipt is not None
    assert (await executor.execute(edit, actor)).replayed
    undone = await executor.execute(saved.receipt.inverse(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    current = await service.get_accessible(draft.snapshot.id, actor)
    assert "display_name" not in current.snapshot.answers
    await service.apply_change(
        draft.snapshot.id,
        actor,
        DraftChange(
            current.snapshot.revision,
            "other-client",
            DraftChangeKey(str(uuid4())),
            (FieldOperation(uuid4(), "description", FieldOperationKind.SET, "Another answer"),),
        ),
        locale=None,
    )
    with pytest.raises(RevisionConflictError):
        await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SubmissionDraftChange)) == 3
