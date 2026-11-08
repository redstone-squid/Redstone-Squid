"""Answers retained while an account prepares a build submission.

A DraftSnapshot is the current set of answers. A DraftChange describes one edit
against a particular snapshot revision, so an older client cannot overwrite a
newer edit. The form manifest defines which fields and values are allowed.
The types in this module enforce edit structure, revision checks, and limits
on retained answers.
"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import UUID

from squid.core.errors import ConflictError, JSONValue, ValidationError
from squid.core.i18n import tr

_FIELD_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DRAFT_CHANGE_KEY_MIN_LENGTH = 8
DRAFT_CHANGE_KEY_MAX_LENGTH = 255
DRAFT_CHANGE_KEY_PATTERN = rf"^[\x21-\x7e]{{{DRAFT_CHANGE_KEY_MIN_LENGTH},{DRAFT_CHANGE_KEY_MAX_LENGTH}}}$"
_IDEMPOTENCY_KEY = re.compile(DRAFT_CHANGE_KEY_PATTERN)
MAX_DRAFT_OPERATIONS = 100
MAX_DRAFT_OPERATION_VALUE_BYTES = 16 * 1024
MAX_DRAFT_ANSWERS_BYTES = 64 * 1024
MAX_DRAFT_JSON_DEPTH = 4
MAX_DRAFT_JSON_NODES = 1_024


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


class DraftChangeKey(str):
    """A retry identity for one atomic draft edit."""

    def __new__(cls, value: str) -> DraftChangeKey:
        if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            msg = tr("draft change keys must be 8-255 visible ASCII characters")
            raise ValidationError(msg)
        return super().__new__(cls, value)


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
    """Set or remove one field, identified by its manifest ID."""

    operation_id: UUID
    field_id: str
    kind: FieldOperationKind
    value: JSONValue = None

    def __post_init__(self) -> None:
        if _FIELD_ID.fullmatch(self.field_id) is None:
            field_id = self.field_id
            raise ValidationError(tr(t"invalid draft field ID: {field_id}"))
        if self.kind is FieldOperationKind.UNSET and self.value is not None:
            msg = tr("unset operations cannot carry a value")
            raise ValidationError(msg)
        if self.kind is FieldOperationKind.SET:
            _require_json_budget(
                self.value,
                max_bytes=MAX_DRAFT_OPERATION_VALUE_BYTES,
                max_depth=MAX_DRAFT_JSON_DEPTH,
            )


@dataclass(frozen=True, slots=True)
class DraftChange:
    """A batch of field edits with an expected revision and a retry key.

    Each field may appear once. Reuse the idempotency key when retrying the same
    batch; replay detection belongs to persistence, not to DraftSnapshot.apply.
    """

    base_revision: int
    client_instance_id: str
    idempotency_key: DraftChangeKey
    operations: tuple[FieldOperation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", DraftChangeKey(self.idempotency_key))
        if self.base_revision < 0:
            msg = tr("base_revision cannot be negative")
            raise ValidationError(msg)
        if _CLIENT_ID.fullmatch(self.client_instance_id) is None:
            msg = tr("client_instance_id has an invalid format")
            raise ValidationError(msg)
        if not self.operations:
            msg = tr("draft changes require at least one operation")
            raise ValidationError(msg)
        if len(self.operations) > MAX_DRAFT_OPERATIONS:
            limit = MAX_DRAFT_OPERATIONS
            raise ValidationError(tr(t"draft changes cannot exceed {limit} operations"))
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            msg = tr("operation IDs must be unique within a draft change")
            raise ValidationError(msg)
        field_ids = [operation.field_id for operation in self.operations]
        if len(field_ids) != len(set(field_ids)):
            msg = tr("a draft change may mutate each field at most once")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    """Current answers and revision of an account-owned draft.

    schema_revision selects the form definition; revision counts edits to this
    draft. These identify different things: editing an answer does not change
    the form definition that the draft uses.

    Construction copies the answers so later changes to the input mapping cannot
    change this snapshot. The stored mapping itself is still mutable; use apply
    to produce a new revision rather than editing it directly.
    """

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
            msg = tr("owner_account_id must be positive")
            raise ValidationError(msg)
        if self.schema_revision < 1 or self.revision < 0:
            msg = tr("schema_revision must be positive and revision cannot be negative")
            raise ValidationError(msg)
        _require_json_budget(
            self.answers,
            max_bytes=MAX_DRAFT_ANSWERS_BYTES,
            max_depth=MAX_DRAFT_JSON_DEPTH + 1,
        )
        object.__setattr__(self, "answers", deepcopy(dict(self.answers)))

    def apply(self, change: DraftChange) -> DraftSnapshot:
        """Return a new editing snapshot with the revision incremented by one.

        Only editing and needs-attention drafts accept changes. A stale base
        revision raises DraftRevisionConflictError. This checks JSON budgets but
        does not validate field names or values against a form manifest.
        """
        if self.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            current_status = self.status.value
            raise ValidationError(
                tr(t"drafts in {current_status} state cannot be edited"),
                resource="submission_draft",
                public_context={"status": self.status.value},
            )
        if change.base_revision != self.revision:
            raise DraftRevisionConflictError(expected=change.base_revision, actual=self.revision)
        answers = deepcopy(dict(self.answers))
        for operation in change.operations:
            if operation.kind is FieldOperationKind.SET:
                answers[operation.field_id] = deepcopy(operation.value)
            else:
                answers.pop(operation.field_id, None)
        try:
            _require_json_budget(
                answers,
                max_bytes=MAX_DRAFT_ANSWERS_BYTES,
                max_depth=MAX_DRAFT_JSON_DEPTH + 1,
            )
        except TypeError, ValueError:
            msg = tr("The draft answers exceed the retained data limit.")
            raise ValidationError(
                msg,
                resource="submission_draft",
                public_context={"reason": "answers_too_large"},
            ) from None
        return replace(self, revision=self.revision + 1, status=DraftStatus.EDITING, answers=answers)

    def transition(self, status: DraftStatus) -> DraftSnapshot:
        """Return a snapshot with a new lifecycle status, leaving its revision unchanged."""
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
            current = self.status.value
            next_status = status.value
            raise ValidationError(
                tr(t"draft cannot transition from {current} to {next_status}"),
                resource="submission_draft",
            )
        return replace(self, status=status)


def _require_json_budget(value: object, *, max_bytes: int, max_depth: int) -> None:
    """Reject oversized or deeply nested JSON before copying or retaining it.

    The first pass bounds traversal and estimates size; JSON encoding then checks
    the exact byte count. Reusing a container is rejected even without a cycle.
    """
    stack: list[tuple[object, int]] = [(value, 0)]
    containers: set[int] = set()
    nodes = 0
    estimated_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_DRAFT_JSON_NODES:
            msg = tr("draft JSON values contain too many nodes")
            raise ValidationError(msg)
        if depth > max_depth:
            msg = tr("draft JSON values are nested too deeply")
            raise ValidationError(msg)
        if item is None or isinstance(item, bool):
            estimated_bytes += 5
        elif isinstance(item, str):
            estimated_bytes += len(item.encode("utf-8")) + 2
        elif isinstance(item, int):
            if item.bit_length() > 256:
                msg = tr("draft JSON integers are too large")
                raise ValidationError(msg)
            estimated_bytes += 80
        elif isinstance(item, float):
            if not math.isfinite(item):
                msg = tr("draft JSON numbers must be finite")
                raise ValidationError(msg)
            estimated_bytes += 32
        elif isinstance(item, Mapping):
            _require_new_container(item, containers)
            estimated_bytes += 2 + len(item)
            for key, nested in item.items():
                if not isinstance(key, str):
                    msg = tr("draft JSON object keys must be strings")
                    raise ValidationError(msg)
                estimated_bytes += len(key.encode("utf-8")) + 3
                stack.append((nested, depth + 1))
        elif isinstance(item, Sequence) and not isinstance(item, str | bytes | bytearray):
            _require_new_container(item, containers)
            estimated_bytes += 2 + len(item)
            stack.extend((nested, depth + 1) for nested in item)
        else:
            msg = tr("draft values must be JSON-compatible")
            raise ValidationError(msg)
        if estimated_bytes > max_bytes:
            msg = tr("draft JSON values exceed the retained byte limit")
            raise ValidationError(msg)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (TypeError, ValueError, RecursionError) as error:
        msg = tr("draft values must be JSON-compatible")
        raise ValidationError(msg) from error
    if len(encoded) > max_bytes:
        msg = tr("draft JSON values exceed the retained byte limit")
        raise ValidationError(msg)


def _require_new_container(value: object, seen: set[int]) -> None:
    identity = id(value)
    if identity in seen:
        msg = tr("draft JSON values cannot contain cycles or shared containers")
        raise ValidationError(msg)
    seen.add(identity)
