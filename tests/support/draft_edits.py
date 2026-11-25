"""Record draft commands in transport tests; database tests cover atomic outcomes."""

from typing import Protocol, override
from uuid import UUID

from squid.core.actors import ActingContext
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import (
    EditCommand,
    EditOutcome,
    EditReceipt,
    EditResult,
    Revision,
    RevisionConflictError,
)
from squid.core.submissions.application.drafts import AppliedDraftChange, StoredDraft
from squid.core.submissions.application.edits import answer_change, captured_answers
from squid.core.submissions.domain import DraftChange
from tests.support.edits import bound_revisions


class RecordedDrafts(Protocol):
    async def get_accessible(self, draft_id: UUID, *, context: ActingContext) -> StoredDraft: ...

    async def apply_change(
        self, draft_id: UUID, change: DraftChange, *, context: ActingContext, locale: str | None
    ) -> AppliedDraftChange: ...


class DraftEditRecorder(EditRunner):
    def __init__(self, drafts: RecordedDrafts) -> None:
        self.drafts = drafts

    @override
    async def execute(self, command: EditCommand, *, context: ActingContext) -> EditResult:
        actor = context.effective
        expected = bound_revisions(command)[0]
        draft_id = UUID(expected.target.key)
        current = await self.drafts.get_accessible(draft_id, context=context)
        if expected.value != current.snapshot.revision:
            raise RevisionConflictError(expected, current.snapshot.revision)
        change = answer_change(command, expected.value)
        fields = tuple(op.field_id for op in change.operations)
        before = captured_answers(current.snapshot.answers, fields)
        result = await self.drafts.apply_change(draft_id, change, locale=None, context=context)
        revisions = (Revision(expected.target, result.draft.snapshot.revision),)
        after = captured_answers(result.draft.snapshot.answers, fields)
        receipt = EditReceipt(command, before, after, revisions)
        return EditResult(
            EditOutcome(
                command.operation_id, actor.key, command.fingerprint, revisions, changed=before != after, final=False
            ),
            receipt,
        )
