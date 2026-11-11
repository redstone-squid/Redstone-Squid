"""Record draft commands in transport tests; database tests cover atomic outcomes."""

from typing import Protocol
from uuid import UUID

from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import (
    EditAttempt,
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    Revision,
    RevisionConflictError,
)
from squid.submissions.application.drafts import AppliedDraftChange, DraftActor, StoredDraft
from squid.submissions.application.edits import answer_change, captured_answers
from squid.submissions.domain import DraftChange


class RecordedDrafts(Protocol):
    async def get_accessible(self, draft_id: UUID, actor: DraftActor, /) -> StoredDraft: ...

    async def apply_change(
        self, draft_id: UUID, actor: DraftActor, change: DraftChange, /, *, locale: str | None
    ) -> AppliedDraftChange: ...


class DraftEditRecorder(EditRunner):
    def __init__(self, drafts: RecordedDrafts) -> None:
        self.drafts = drafts

    async def execute(self, command: EditCommand, actor: Actor, *, attempt: EditAttempt | None = None) -> EditResult:
        draft_id = UUID(command.checked_revisions[0].target.key)
        current = await self.drafts.get_accessible(draft_id, actor)
        expected = command.checked_revisions[0]
        if expected.value != current.snapshot.revision:
            raise RevisionConflictError(expected, current.snapshot.revision)
        change = answer_change(command)
        fields = tuple(op.field_id for op in change.operations)
        before = captured_answers(current.snapshot.answers, fields)
        result = await self.drafts.apply_change(draft_id, actor, change, locale=None)
        revisions = (Revision(expected.target, result.draft.snapshot.revision),)
        after = captured_answers(result.draft.snapshot.answers, fields)
        receipt = EditReceipt(command, before, after, revisions)
        if attempt is not None:
            attempt.receipt = receipt
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, revisions, changed=before != after, final=False
            ),
            receipt,
        )

    async def reconcile(self, command: EditCommand, actor: Actor, attempt: EditAttempt) -> EditResult | None:
        raise NotImplementedError
