"""Describe why a reactive transaction ran and what it committed or rolled back.

ActionContext gives the transaction an identity and links it to the event that
caused it. A completed transaction produces an ActionCommit or ActionRollback;
history can use the commit's reversible changes, while diagnostic sinks receive
portable summaries. Operations and resource loads carry causal references so
their later results can still be traced to the initiating action.
"""

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    # Transactions import action results at runtime; their patch type is annotation-only here.
    from squid_reactivity.transactions import (
        CellPatchSet,
    )

type ActionId = uuid.UUID
_EMPTY_METADATA: Mapping[str, str] = MappingProxyType({})


class ActionPurpose(Enum):
    """Why an action was started."""

    NORMAL = "normal"
    UNDO = "undo"
    REDO = "redo"
    COMPENSATION = "compensation"
    REMOTE = "remote"
    RECOVERY = "recovery"
    SYSTEM = "system"


class RollbackReason(Enum):
    """Why an admitted action published nothing."""

    HANDLER_EXCEPTION = "handler_exception"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    VALIDATION_FAILED = "validation_failed"
    PARTICIPANT_PREPARE_FAILED = "participant_prepare_failed"
    FRAMEWORK_INTEGRITY_FAILURE = "framework_integrity_failure"


@dataclass(frozen=True, slots=True)
class CausalRef:
    """A stable reference to an action, operation, resource generation, or system event."""

    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class ActorRef:
    """An application-defined actor identifier included in diagnostics."""

    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class ActionRelation:
    """The history action this transaction reverses, reapplies, or compensates."""

    kind: Literal["reverses", "reapplies", "compensates"]
    action_id: ActionId


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Immutable identity and causality assigned before an action begins."""

    action_id: ActionId
    cause: CausalRef | None
    root_action_id: ActionId
    kind: ActionPurpose
    name: str
    actor: ActorRef | None
    started_at: datetime
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)
    relation: ActionRelation | None = None

    @classmethod
    def create(
        cls,
        name: str = "action",
        *,
        kind: ActionPurpose = ActionPurpose.NORMAL,
        cause: CausalRef | None = None,
        root_action_id: ActionId | None = None,
        actor: ActorRef | None = None,
        metadata: Mapping[str, str] | None = None,
        relation: ActionRelation | None = None,
    ) -> ActionContext:
        action_id = uuid.uuid7()
        return cls(
            action_id,
            cause,
            root_action_id or action_id,
            kind,
            name,
            actor,
            datetime.now(UTC),
            _EMPTY_METADATA if not metadata else MappingProxyType(dict(metadata)),
            relation,
        )

    def causal_ref(self) -> CausalRef:
        """Return the immutable token detached work may retain."""
        return CausalRef("action", str(self.action_id))


@dataclass(frozen=True, slots=True)
class CommitSequence:
    """A runtime-local diagnostic order, never comparable across runtimes."""

    runtime_id: uuid.UUID
    value: int


@dataclass(frozen=True, slots=True)
class ExceptionReport:
    """An exception type and message without references to its traceback.

    The message is captured unchanged and may contain sensitive data. Apply a
    RedactionPolicy before storing or sharing the report.
    """

    type_name: str
    message: str

    @classmethod
    def capture(cls, error: BaseException) -> ExceptionReport:
        return cls(type(error).__name__, str(error))


@dataclass(frozen=True, slots=True)
class ObservedRead:
    """The committed version of a shared cell read by an action."""

    target_id: str
    version: int


@dataclass(frozen=True, slots=True)
class ChangeReport:
    """A safe count-only projection of staged or committed changes."""

    cells: int = 0
    participants: int = 0


@dataclass(frozen=True, slots=True)
class ConflictDetail:
    """A target identifier and the versions that caused a conflict."""

    target_id: str
    expected_version: int
    actual_version: int


class ChangeToken[InverseT = Any](Protocol):
    """A participant's reversible change.

    The participant defines the inverse type. History first asks plan_inverse
    whether the change can still be undone, then passes the returned inverse
    to stage_inverse inside a transaction.
    """

    def plan_inverse(self) -> InverseT | ConflictDetail: ...

    def stage_inverse(self, inverse: InverseT) -> None: ...


@dataclass(frozen=True, slots=True)
class TransactionContribution:
    """An opaque reversible participant contribution and its safe report."""

    participant_id: str
    token: ChangeToken[Any]
    report: ChangeReport = ChangeReport(participants=1)


@dataclass(frozen=True, slots=True)
class ActionCommit:
    """The immutable in-process record of an action that crossed the commit point."""

    context: ActionContext
    sequence: CommitSequence
    committed_at: datetime
    duration: timedelta
    reads: tuple[ObservedRead, ...]
    patches: CellPatchSet
    participant_changes: tuple[TransactionContribution, ...] = ()
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ActionRollback:
    """The result of an action that discarded its staged writes."""

    context: ActionContext
    rolled_back_at: datetime
    duration: timedelta
    reason: RollbackReason
    reads: tuple[ObservedRead, ...]
    conflict: ConflictDetail | None
    exception: ExceptionReport | None
    staged_report: ChangeReport
    cleanup_errors: tuple[ExceptionReport, ...] = ()


type ActionResult = ActionCommit | ActionRollback


_RUNTIME_ID = uuid.uuid4()
_sequence = 0


_CURRENT_ACTION: ContextVar[ActionContext | None] = ContextVar("squid_reactivity_action", default=None)
_CURRENT_CAUSALITY: ContextVar[tuple[CausalRef, ActionId | None] | None] = ContextVar(
    "squid_reactivity_causality", default=None
)
_continuation_depth: ContextVar[int] = ContextVar("squid_reactivity_continuation_depth", default=0)


def next_commit_sequence() -> CommitSequence:
    global _sequence
    _sequence += 1
    return CommitSequence(_RUNTIME_ID, _sequence)


def current_action() -> ActionContext | None:
    """Return the action context installed in this context, if any."""
    return _CURRENT_ACTION.get()


def current_causality() -> tuple[CausalRef, ActionId | None] | None:
    """Return the immutable cause/root pair installed for action, operation, or resource work."""
    return _CURRENT_CAUSALITY.get()


@contextmanager
def action_scope(context: ActionContext) -> Iterator[ActionContext]:
    """Install an action context until the with block exits."""
    token = _CURRENT_ACTION.set(context)
    causal = _CURRENT_CAUSALITY.set((context.causal_ref(), context.root_action_id))
    try:
        yield context
    finally:
        _CURRENT_CAUSALITY.reset(causal)
        _CURRENT_ACTION.reset(token)


@contextmanager
def causal_scope(cause: CausalRef, root_action_id: ActionId | None) -> Iterator[None]:
    """Set cause and root identifiers without opening a transaction."""
    token = _CURRENT_CAUSALITY.set((cause, root_action_id))
    try:
        yield
    finally:
        _CURRENT_CAUSALITY.reset(token)


@contextmanager
def continuation_callback() -> Iterator[None]:
    """Mark this with block as continuation work after an action result."""
    token = _continuation_depth.set(_continuation_depth.get() + 1)
    try:
        yield
    finally:
        _continuation_depth.reset(token)


def in_continuation() -> bool:
    return _continuation_depth.get() > 0
