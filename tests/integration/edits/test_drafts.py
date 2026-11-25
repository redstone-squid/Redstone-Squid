"""Draft history shares revision checks, audit changes, and atomic operation outcomes."""

from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.edits.application import EditExecutor
from squid.core.edits.domain import OperationReuseError, RevisionConflictError
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.core.submissions.application.edits import DRAFT_ANSWERS, DraftAnswersEdit, change_command
from squid.core.submissions.application.forms import (
    CURRENT_SUBMISSION_SCHEMA,
    CURRENT_SUBMISSION_SCHEMA_REVISION,
    CheckedInFormManifestRegistry,
)
from squid.core.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftSnapshot,
    FieldOperation,
    FieldOperationKind,
    SubmissionOrigin,
)
from squid.core.submissions.models.entities import SubmissionDraftChange


async def test_answers_undo_replay_and_external_change_conflict(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with sessions.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        actor = UserActor(account_id=account.id)
    now = Instant.now()
    draft = StoredDraft(
        DraftSnapshot(
            uuid4(),
            account.id,
            CURRENT_SUBMISSION_SCHEMA,
            CURRENT_SUBMISSION_SCHEMA_REVISION,
            "other",
            answers={"description": None},
        ),
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
    client_change = DraftChange(
        0,
        "web:editor",
        DraftChangeKey("web-change-0001"),
        (
            FieldOperation(uuid4(), "display_name", FieldOperationKind.SET, "First"),
            FieldOperation(uuid4(), "description", FieldOperationKind.SET, "Description"),
        ),
    )
    edit = change_command(draft.snapshot.id, client_change)
    saved = await executor.execute(edit, context=ActingContext.direct(actor))
    assert saved.receipt is not None
    assert (
        await executor.execute(change_command(draft.snapshot.id, client_change), context=ActingContext.direct(actor))
    ).replayed
    with pytest.raises(OperationReuseError):
        await executor.execute(
            change_command(draft.snapshot.id, replace(client_change, client_instance_id="different-client")),
            context=ActingContext.direct(actor),
        )
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), context=ActingContext.direct(actor))
    assert undone.receipt is not None
    current = await service.get_accessible(draft.snapshot.id, context=ActingContext.for_actor(actor))
    assert "display_name" not in current.snapshot.answers
    assert current.snapshot.answers["description"] is None
    await service.apply_change(
        draft.snapshot.id,
        DraftChange(
            current.snapshot.revision,
            "other-client",
            DraftChangeKey(str(uuid4())),
            (FieldOperation(uuid4(), "description", FieldOperationKind.SET, "Another answer"),),
        ),
        context=ActingContext.for_actor(actor),
        locale=None,
    )
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.redo(undone.outcome.revisions), context=ActingContext.direct(actor))
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SubmissionDraftChange)) == 3
        first = await session.scalar(select(SubmissionDraftChange).where(SubmissionDraftChange.resulting_revision == 1))
        assert first is not None
        assert first.client_instance_id == client_change.client_instance_id
        assert first.idempotency_key == client_change.idempotency_key
        assert {op["operation_id"] for op in first.operations} == {
            str(op.operation_id) for op in client_change.operations
        }
