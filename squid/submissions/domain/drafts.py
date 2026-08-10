"""Revisioned submission draft value objects."""

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID

from squid.core.errors import ConflictError, JSONValue, ValidationError

_FIELD_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{8,255}$")


class DraftStatus(StrEnum):
    """Lifecycle state of a server-side submission draft."""

    EDITING = "editing"
    PROCESSING = "processing"
    NEEDS_ATTENTION = "needs_attention"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


class FieldOperationKind(StrEnum):
    """Mutations supported by the v1 field-operation protocol."""

    SET = "set"
    UNSET = "unset"


class DraftRevisionConflictError(ConflictError):
    """A client attempted to mutate a stale draft revision."""

    default_message = "The draft changed in another client. Reload it before saving."
    default_title = "Draft changed"
    default_resource = "submission_draft"

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            context={"expected_revision": expected, "actual_revision": actual},
            public_context={"expected_revision": expected, "actual_revision": actual},
        )


@dataclass(frozen=True, slots=True)
class FieldOperation:
    """One stable-field mutation in a draft change."""

    operation_id: UUID
    field_id: str
    kind: FieldOperationKind
    value: JSONValue = None

    def __post_init__(self) -> None:
        if _FIELD_ID.fullmatch(self.field_id) is None:
            msg = f"invalid draft field ID: {self.field_id}"
            raise ValueError(msg)
        if self.kind is FieldOperationKind.UNSET and self.value is not None:
            msg = "unset operations cannot carry a value"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DraftChange:
    """An atomic client edit carrying concurrency and replay metadata."""

    base_revision: int
    client_instance_id: str
    idempotency_key: str
    operations: tuple[FieldOperation, ...]

    def __post_init__(self) -> None:
        if self.base_revision < 0:
            msg = "base_revision cannot be negative"
            raise ValueError(msg)
        if _CLIENT_ID.fullmatch(self.client_instance_id) is None:
            msg = "client_instance_id has an invalid format"
            raise ValueError(msg)
        if _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key) is None:
            msg = "idempotency_key must be 8-255 visible ASCII characters"
            raise ValueError(msg)
        if not self.operations:
            msg = "draft changes require at least one operation"
            raise ValueError(msg)
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            msg = "operation IDs must be unique within a draft change"
            raise ValueError(msg)
        field_ids = [operation.field_id for operation in self.operations]
        if len(field_ids) != len(set(field_ids)):
            msg = "a draft change may mutate each field at most once"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    """Current compacted state of one account-owned draft."""

    id: UUID
    owner_account_id: int
    schema_id: str
    schema_revision: int
    category: str
    revision: int = 0
    status: DraftStatus = DraftStatus.EDITING
    answers: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.owner_account_id < 1:
            msg = "owner_account_id must be positive"
            raise ValueError(msg)
        if self.schema_revision < 1 or self.revision < 0:
            msg = "schema_revision must be positive and revision cannot be negative"
            raise ValueError(msg)
        object.__setattr__(self, "answers", deepcopy(dict(self.answers)))

    def apply(self, change: DraftChange) -> "DraftSnapshot":
        """Apply an atomic edit or reject it when the client is stale."""
        if self.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            msg = f"drafts in {self.status.value} state cannot be edited"
            raise ValidationError(msg, resource="submission_draft", public_context={"status": self.status.value})
        if change.base_revision != self.revision:
            raise DraftRevisionConflictError(expected=change.base_revision, actual=self.revision)
        answers = deepcopy(dict(self.answers))
        for operation in change.operations:
            if operation.kind is FieldOperationKind.SET:
                answers[operation.field_id] = deepcopy(operation.value)
            else:
                answers.pop(operation.field_id, None)
        return replace(self, revision=self.revision + 1, status=DraftStatus.EDITING, answers=answers)

    def transition(self, status: DraftStatus) -> "DraftSnapshot":
        """Apply an allowed lifecycle transition."""
        allowed: dict[DraftStatus, frozenset[DraftStatus]] = {
            DraftStatus.EDITING: frozenset({DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION, DraftStatus.EXPIRED}),
            DraftStatus.PROCESSING: frozenset(
                {DraftStatus.SUBMITTED, DraftStatus.NEEDS_ATTENTION, DraftStatus.EXPIRED}
            ),
            DraftStatus.NEEDS_ATTENTION: frozenset({DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.EXPIRED}),
            DraftStatus.SUBMITTED: frozenset(),
            DraftStatus.EXPIRED: frozenset(),
        }
        if status not in allowed[self.status]:
            msg = f"draft cannot transition from {self.status.value} to {status.value}"
            raise ValidationError(msg, resource="submission_draft")
        return replace(self, status=status)
