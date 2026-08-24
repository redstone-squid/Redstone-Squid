"""Action identity, immutable outcomes, and bounded diagnostic retention."""

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

_log = logging.getLogger(__name__)

type ActionId = uuid.UUID


class ActionKind(Enum):
    """The semantic reason an action exists."""

    ACTION = "action"
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
    """A safe application-defined actor identity."""

    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Immutable identity and causality assigned before an action begins."""

    action_id: ActionId
    cause: CausalRef | None
    root_action_id: ActionId
    kind: ActionKind
    name: str
    actor: ActorRef | None
    started_at: datetime
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    reverses_action_id: ActionId | None = None
    reapplies_action_id: ActionId | None = None
    compensates_action_id: ActionId | None = None

    @classmethod
    def create(
        cls,
        name: str = "action",
        *,
        kind: ActionKind = ActionKind.ACTION,
        cause: CausalRef | None = None,
        root_action_id: ActionId | None = None,
        actor: ActorRef | None = None,
        metadata: Mapping[str, str] | None = None,
        reverses_action_id: ActionId | None = None,
        reapplies_action_id: ActionId | None = None,
        compensates_action_id: ActionId | None = None,
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
            MappingProxyType(dict(metadata or {})),
            reverses_action_id,
            reapplies_action_id,
            compensates_action_id,
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
class ExceptionSummary:
    """A retention-safe exception description without traceback or arbitrary values."""

    type_name: str
    message: str

    @classmethod
    def capture(cls, error: BaseException) -> ExceptionSummary:
        return cls(type(error).__name__, str(error))


@dataclass(frozen=True, slots=True)
class ObservedRead:
    """One strong input version used by a publishing action."""

    target_id: str
    version: int


@dataclass(frozen=True, slots=True)
class ChangeSummary:
    """A safe count-only projection of staged or committed changes."""

    cells: int = 0
    participants: int = 0


@dataclass(frozen=True, slots=True)
class ConflictDetail:
    """A stable target and its expected and actual lineage."""

    target_id: str
    expected_version: int
    actual_version: int


@dataclass(frozen=True, slots=True)
class ParticipantChange:
    """An opaque reversible participant contribution and its safe summary."""

    participant_id: str
    token: Any
    summary: ChangeSummary = ChangeSummary(participants=1)


@dataclass(frozen=True, slots=True)
class ActionCommit:
    """The immutable in-process record of an action that crossed the commit point."""

    context: ActionContext
    sequence: CommitSequence
    committed_at: datetime
    duration: timedelta
    reads: tuple[ObservedRead, ...]
    patches: Any
    participant_changes: tuple[ParticipantChange, ...] = ()
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ActionRollback:
    """The immutable record of an action whose transaction is dead and published nothing."""

    context: ActionContext
    rolled_back_at: datetime
    duration: timedelta
    reason: RollbackReason
    reads: tuple[ObservedRead, ...]
    conflict: ConflictDetail | None
    exception: ExceptionSummary | None
    staged_summary: ChangeSummary
    cleanup_errors: tuple[ExceptionSummary, ...] = ()


type ActionOutcome = ActionCommit | ActionRollback


@dataclass(frozen=True, slots=True)
class ActionOutcomeSnapshot:
    """The bounded, portable default projection of an outcome."""

    action_id: str
    root_action_id: str
    cause: CausalRef | None
    kind: str
    name: str
    terminal: str
    timestamp: datetime
    sequence: CommitSequence | None
    reason: str | None
    changes: ChangeSummary

    @classmethod
    def from_outcome(cls, outcome: ActionOutcome) -> ActionOutcomeSnapshot:
        context = outcome.context
        if isinstance(outcome, ActionCommit):
            changes = ChangeSummary(len(outcome.patches), len(outcome.participant_changes))
            return cls(
                str(context.action_id),
                str(context.root_action_id),
                context.cause,
                context.kind.value,
                context.name,
                "committed",
                outcome.committed_at,
                outcome.sequence,
                None,
                changes,
            )
        return cls(
            str(context.action_id),
            str(context.root_action_id),
            context.cause,
            context.kind.value,
            context.name,
            "rolled_back",
            outcome.rolled_back_at,
            None,
            outcome.reason.value,
            outcome.staged_summary,
        )


class ActionOutcomeSink(Protocol):
    """A synchronous consumer of retention-safe action outcomes."""

    def accept(self, outcome: ActionOutcomeSnapshot) -> None: ...


class ActionLedger:
    """A bounded diagnostic ledger. Calling :meth:`close` ends its sink registration."""

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            message = "an action ledger needs room for at least one outcome"
            raise ValueError(message)
        self._outcomes: deque[ActionOutcomeSnapshot] = deque(maxlen=limit)

    def accept(self, outcome: ActionOutcomeSnapshot) -> None:
        self._outcomes.append(outcome)

    @property
    def outcomes(self) -> tuple[ActionOutcomeSnapshot, ...]:
        return tuple(self._outcomes)

    def close(self) -> None:
        remove_action_outcome_sink(self)


_RUNTIME_ID = uuid.uuid4()
_sequence = 0
_sinks: list[ActionOutcomeSink] = []
_CURRENT_ACTION: ContextVar[ActionContext | None] = ContextVar("squid_reactive_action", default=None)
_aftermath_depth: ContextVar[int] = ContextVar("squid_reactive_aftermath_depth", default=0)


def next_commit_sequence() -> CommitSequence:
    global _sequence
    _sequence += 1
    return CommitSequence(_RUNTIME_ID, _sequence)


def current_action() -> ActionContext | None:
    """Return the live lexical action context, if any."""
    return _CURRENT_ACTION.get()


@contextmanager
def action_scope(context: ActionContext):
    """Install an action context until the lexical scope exits."""
    token = _CURRENT_ACTION.set(context)
    try:
        yield context
    finally:
        _CURRENT_ACTION.reset(token)


def add_action_outcome_sink(sink: ActionOutcomeSink) -> None:
    if sink not in _sinks:
        _sinks.append(sink)


def remove_action_outcome_sink(sink: ActionOutcomeSink) -> None:
    if sink in _sinks:
        _sinks.remove(sink)


def emit_outcome(outcome: ActionOutcome) -> None:
    snapshot = ActionOutcomeSnapshot.from_outcome(outcome)
    for sink in tuple(_sinks):
        try:
            sink.accept(snapshot)
        except Exception:
            _log.exception("an action outcome sink failed")


class Aftermath:
    """Authority to start fresh causal work after an outcome; the callback ends it."""

    def __init__(self, outcome: ActionOutcome) -> None:
        self.outcome = outcome

    @contextmanager
    def start_action(self, name: str, *, kind: ActionKind = ActionKind.RECOVERY):
        """Start a fresh causal action context for a new transaction."""
        context = ActionContext.create(
            name,
            kind=kind,
            cause=self.outcome.context.causal_ref(),
            root_action_id=self.outcome.context.root_action_id,
        )
        with action_scope(context):
            yield context

    def start_operation(self, start: Callable[[CausalRef, ActionId], Any]) -> Any:
        """Start application-owned work with immutable cause and root identifiers."""
        return start(self.outcome.context.causal_ref(), self.outcome.context.root_action_id)


@contextmanager
def aftermath_callback():
    token = _aftermath_depth.set(_aftermath_depth.get() + 1)
    try:
        yield
    finally:
        _aftermath_depth.reset(token)


def in_aftermath() -> bool:
    return _aftermath_depth.get() > 0


def elapsed(context: ActionContext) -> timedelta:
    return datetime.now(UTC) - context.started_at


def monotonic_started() -> float:
    """Return a monotonic marker for adapters that need one without retaining it in outcomes."""
    return time.monotonic()
