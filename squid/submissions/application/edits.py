"""Draft answer commands reuse manifest validation and the existing change audit."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from squid.core.actors import Actor
from squid.core.errors import JSONValue, ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditTarget, EditValues, PreparedEdit, Revision, RevisionConflictError
from squid.submissions.application.drafts import DraftUnitOfWork, StoredDraft, SubmissionDraftService
from squid.submissions.domain import DraftChange, DraftChangeKey, FieldOperation, FieldOperationKind

DRAFT_ANSWERS = "draft.answers"


def draft_target(draft_id: UUID) -> EditTarget:
    return EditTarget("draft", str(draft_id))


class AnswerPatch(BaseModel):
    """Explicit removals preserve the difference between an absent answer and JSON null."""

    model_config = ConfigDict(extra="forbid")

    answers: dict[str, JsonValue] = Field(default_factory=dict)
    unset: tuple[str, ...] = ()
    client_instance_id: str = "versioned-editor"
    change_key: str | None = None
    operation_ids: dict[str, UUID] = Field(default_factory=dict)

    def values(self) -> EditValues:
        return EditValues(self.model_dump_json(exclude_defaults=True))


def answer_values(answers: Mapping[str, JSONValue]) -> EditValues:
    """Encode UI answers, where an empty input removes the saved answer."""
    return AnswerPatch(
        answers={key: value for key, value in answers.items() if value is not None},
        unset=tuple(key for key, value in answers.items() if value is None),
    ).values()


def captured_answers(answers: Mapping[str, JSONValue], fields: tuple[str, ...]) -> EditValues:
    return AnswerPatch(
        answers={key: answers[key] for key in fields if key in answers},
        unset=tuple(key for key in fields if key not in answers),
    ).values()


def change_command(draft_id: UUID, change: DraftChange) -> EditCommand:
    """Retain the existing client retry identity and audit metadata at the command boundary."""
    patch = AnswerPatch(
        answers={op.field_id: op.value for op in change.operations if op.kind is FieldOperationKind.SET},
        unset=tuple(op.field_id for op in change.operations if op.kind is FieldOperationKind.UNSET),
        client_instance_id=change.client_instance_id,
        change_key=change.idempotency_key,
        operation_ids={op.field_id: op.operation_id for op in change.operations},
    )
    return EditCommand(
        DRAFT_ANSWERS,
        (Revision(draft_target(draft_id), change.base_revision),),
        patch.values(),
        operation_id=uuid5(draft_id, change.idempotency_key),
    )


def answer_change(command: EditCommand) -> DraftChange:
    patch = AnswerPatch.model_validate_json(command.values.encoded)
    if set(patch.unset).intersection(patch.answers):
        raise ValidationError
    operations = tuple(
        FieldOperation(patch.operation_ids.get(key, uuid4()), key, FieldOperationKind.SET, value)
        for key, value in patch.answers.items()
    ) + tuple(
        FieldOperation(patch.operation_ids.get(key, uuid4()), key, FieldOperationKind.UNSET) for key in patch.unset
    )
    return DraftChange(
        command.checked_revisions[0].value,
        patch.client_instance_id,
        DraftChangeKey(patch.change_key or str(command.operation_id)),
        operations,
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
        expected = command.checked_revisions[0]
        current = await self._drafts.get_accessible_in(uow, UUID(expected.target.key), actor)
        if current.snapshot.revision != expected.value:
            raise RevisionConflictError(expected, current.snapshot.revision)
        change = answer_change(command)
        candidate = await self._drafts.validate_change(current, change, locale=None)
        fields = tuple(operation.field_id for operation in change.operations)
        return PreparedAnswers(
            captured_answers(current.snapshot.answers, fields),
            captured_answers(candidate.answers, fields),
            draft=current,
            change=change,
        )

    async def apply(
        self, uow: DraftEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedAnswers)
        result = await self._drafts.persist_change_in(uow, prepared.draft, actor, prepared.change)
        return (Revision(command.expected[0].target, result.draft.snapshot.revision),)
