"""Draft history shares revision checks, audit changes, and atomic operation outcomes."""

from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.accounts.models import Account
from squid.core.actors import Actor
from squid.edits.application import EditExecutor
from squid.edits.domain import OperationReuseError, RevisionConflictError
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.submissions.application.drafts import StoredDraft, SubmissionDraftService
from squid.submissions.application.edits import DRAFT_ANSWERS, DraftAnswersEdit, change_command
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
    saved = await executor.execute(edit, actor)
    assert saved.receipt is not None
    assert (await executor.execute(change_command(draft.snapshot.id, client_change), actor)).replayed
    with pytest.raises(OperationReuseError):
        await executor.execute(
            change_command(draft.snapshot.id, replace(client_change, client_instance_id="different-client")), actor
        )
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    current = await service.get_accessible(draft.snapshot.id, actor)
    assert "display_name" not in current.snapshot.answers
    assert current.snapshot.answers["description"] is None
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
        await executor.execute(saved.receipt.redo(undone.outcome.revisions), actor)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SubmissionDraftChange)) == 3
        first = await session.scalar(select(SubmissionDraftChange).where(SubmissionDraftChange.resulting_revision == 1))
        assert first is not None
        assert first.client_instance_id == client_change.client_instance_id
        assert first.idempotency_key == client_change.idempotency_key
        assert {op["operation_id"] for op in first.operations} == {
            str(op.operation_id) for op in client_change.operations
        }
