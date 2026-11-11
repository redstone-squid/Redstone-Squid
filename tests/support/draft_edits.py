"""Record draft commands in transport tests; database tests cover atomic outcomes."""

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
from squid.submissions.application.drafts import SubmissionDraftService
from squid.submissions.application.edits import answer_change, answer_values


class DraftEditRecorder(EditRunner):
    def __init__(self, drafts: SubmissionDraftService) -> None:
        self.drafts = drafts

    async def execute(self, command: EditCommand, actor: Actor, *, attempt: EditAttempt | None = None) -> EditResult:
        draft_id = UUID(command.expected[0].target.key)
        current = await self.drafts.get_accessible(draft_id, actor)
        expected = command.expected[0]
        if expected.value != current.snapshot.revision:
            raise RevisionConflictError(expected, current.snapshot.revision)
        before = answer_values({key: current.snapshot.answers.get(key) for key in command.values.unpack()})
        result = await self.drafts.apply_change(draft_id, actor, answer_change(command), locale=None)
        revisions = (Revision(expected.target, result.draft.snapshot.revision),)
        after = answer_values({key: result.draft.snapshot.answers.get(key) for key in command.values.unpack()})
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
