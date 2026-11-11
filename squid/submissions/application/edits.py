"""Draft answer commands reuse manifest validation and the existing change audit."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from squid.core.actors import Actor
from squid.core.errors import JSONValue, ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision, RevisionConflictError
from squid.submissions.application.drafts import DraftUnitOfWork, StoredDraft, SubmissionDraftService
from squid.submissions.domain import DraftChange, DraftChangeKey, FieldOperation, FieldOperationKind

DRAFT_ANSWERS = "draft.answers"


def draft_target(draft_id: UUID) -> EditTarget:
    return EditTarget("draft", str(draft_id))


def answer_values(answers: Mapping[str, JSONValue]) -> EditValues:
    return EditValues(json.dumps(dict(answers)))


def answer_change(command: EditCommand) -> DraftChange:
    return DraftChange(
        command.expected[0].value,
        "versioned-editor",
        DraftChangeKey(str(command.operation_id)),
        tuple(
            FieldOperation(uuid4(), key, FieldOperationKind.UNSET if value is None else FieldOperationKind.SET, value)
            for key, value in command.values.unpack().items()
        ),
    )


class DraftEditUnitOfWork(DraftUnitOfWork, EditUnitOfWork, Protocol):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedAnswers(PreparedEdit):
    draft: StoredDraft
    change: DraftChange


class DraftAnswersEdit:
    """Manifest-defined fields have dynamic validation rather than a fixed Pydantic schema."""

    def __init__(self, drafts: SubmissionDraftService) -> None:
        self._drafts = drafts

    async def prepare(self, uow: DraftEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedAnswers:
        if len(command.expected) != 1 or command.expected[0].target.kind != "draft":
            raise ValidationError
        expected = command.expected[0]
        current = await self._drafts.get_accessible_in(uow, UUID(expected.target.key), actor)
        if current.snapshot.revision != expected.value:
            raise RevisionConflictError(expected, current.snapshot.revision)
        change = answer_change(command)
        candidate = await self._drafts.validate_change(current, change, locale=None)
        fields = command.values.unpack().keys()
        return PreparedAnswers(
            answer_values({key: current.snapshot.answers.get(key) for key in fields}),
            answer_values({key: candidate.answers.get(key) for key in fields}),
            draft=current,
            change=change,
        )

    async def apply(
        self, uow: DraftEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedAnswers)
        result = await self._drafts.persist_change_in(uow, prepared.draft, actor, prepared.change)
        return (Revision(command.expected[0].target, result.draft.snapshot.revision),)
