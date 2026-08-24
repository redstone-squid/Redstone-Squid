"""Action identity, immutable outcomes, and bounded diagnostic retention."""

import json
import logging
import time
import uuid
import weakref
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
_EMPTY_METADATA: Mapping[str, str] = MappingProxyType({})


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
    metadata: Mapping[str, str] = field(default_factory=lambda: _EMPTY_METADATA)
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
            _EMPTY_METADATA if not metadata else MappingProxyType(dict(metadata)),
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
    tags: frozenset[str] = frozenset()
    actor: ActorRef | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    reverses_action_id: str | None = None
    reapplies_action_id: str | None = None
    compensates_action_id: str | None = None
    conflict: ConflictDetail | None = None
    exception: ExceptionSummary | None = None

    @classmethod
    def from_outcome(
        cls,
        outcome: ActionOutcome,
        policy: RedactionPolicy | None = None,
    ) -> ActionOutcomeSnapshot:
        policy = DEFAULT_REDACTION if policy is None else policy
        context = outcome.context
        common = (
            context.actor if policy.include_actor else None,
            tuple(sorted(context.metadata.items())) if policy.include_metadata else (),
            None if context.reverses_action_id is None else str(context.reverses_action_id),
            None if context.reapplies_action_id is None else str(context.reapplies_action_id),
            None if context.compensates_action_id is None else str(context.compensates_action_id),
        )
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
                outcome.tags,
                *common,
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
            frozenset(),
            *common,
            outcome.conflict,
            policy.exception(outcome.exception),
        )


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Explicit portable-outcome policy for metadata and exception messages."""

    include_actor: bool = True
    include_metadata: bool = False
    include_exception_messages: bool = False

    def exception(self, summary: ExceptionSummary | None) -> ExceptionSummary | None:
        if summary is None or self.include_exception_messages:
            return summary
        return ExceptionSummary(summary.type_name, "[redacted]")


DEFAULT_REDACTION = RedactionPolicy()


@dataclass(frozen=True, slots=True)
class OperationEventSnapshot:
    """One start or terminal transition of a causally identified operation execution."""

    execution_id: str
    root_action_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionSummary | None = None


@dataclass(frozen=True, slots=True)
class ResourceEventSnapshot:
    """One start or terminal transition of a causally identified resource generation."""

    generation_id: str
    root_action_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionSummary | None = None


@dataclass(frozen=True, slots=True)
class AftermathFailureSnapshot:
    """A post-outcome failure that cannot alter the immutable action result."""

    failure_id: str
    root_action_id: str
    cause: CausalRef
    stage: str
    callback: str
    timestamp: datetime
    exception: ExceptionSummary


type CausalEventSnapshot = (
    ActionOutcomeSnapshot | OperationEventSnapshot | ResourceEventSnapshot | AftermathFailureSnapshot
)


class ActionOutcomeCodec:
    """Schema-versioned count-only JSON codec for portable outcome snapshots."""

    schema_version = 1
    max_encoded_bytes = 1_048_576

    def encode(self, outcome: ActionOutcomeSnapshot) -> bytes:
        payload = {
            "schema": self.schema_version,
            "action_id": outcome.action_id,
            "root_action_id": outcome.root_action_id,
            "cause": None
            if outcome.cause is None
            else {"kind": outcome.cause.kind, "identity": outcome.cause.identity},
            "kind": outcome.kind,
            "name": outcome.name,
            "terminal": outcome.terminal,
            "timestamp": outcome.timestamp.isoformat(),
            "sequence": None
            if outcome.sequence is None
            else {"runtime_id": str(outcome.sequence.runtime_id), "value": outcome.sequence.value},
            "reason": outcome.reason,
            "changes": {"cells": outcome.changes.cells, "participants": outcome.changes.participants},
            "tags": sorted(outcome.tags),
            "actor": None
            if outcome.actor is None
            else {"kind": outcome.actor.kind, "identity": outcome.actor.identity},
            "metadata": dict(outcome.metadata),
            "relations": {
                "reverses": outcome.reverses_action_id,
                "reapplies": outcome.reapplies_action_id,
                "compensates": outcome.compensates_action_id,
            },
            "conflict": None
            if outcome.conflict is None
            else {
                "target_id": outcome.conflict.target_id,
                "expected_version": outcome.conflict.expected_version,
                "actual_version": outcome.conflict.actual_version,
            },
            "exception": None
            if outcome.exception is None
            else {"type_name": outcome.exception.type_name, "message": outcome.exception.message},
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def decode(self, data: bytes) -> ActionOutcomeSnapshot:
        if len(data) > self.max_encoded_bytes:
            message = "action outcome exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            return self._decode(data)
        except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            message = "action outcome has a corrupt schema"
            raise ValueError(message) from error
        except ValueError as error:
            if str(error).startswith("unsupported action outcome schema"):
                raise
            message = "action outcome has a corrupt schema"
            raise ValueError(message) from error

    def _decode(self, data: bytes) -> ActionOutcomeSnapshot:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            message = "action outcome has a corrupt schema"
            raise TypeError(message)
        if payload.get("schema") != self.schema_version:
            message = f"unsupported action outcome schema {payload.get('schema')!r}"
            raise ValueError(message)
        cause = payload["cause"]
        sequence = payload["sequence"]
        actor = payload["actor"]
        relations = payload["relations"]
        conflict = payload["conflict"]
        exception = payload["exception"]
        return ActionOutcomeSnapshot(
            payload["action_id"],
            payload["root_action_id"],
            None if cause is None else CausalRef(cause["kind"], cause["identity"]),
            payload["kind"],
            payload["name"],
            payload["terminal"],
            datetime.fromisoformat(payload["timestamp"]),
            None if sequence is None else CommitSequence(uuid.UUID(sequence["runtime_id"]), sequence["value"]),
            payload["reason"],
            ChangeSummary(payload["changes"]["cells"], payload["changes"]["participants"]),
            frozenset(payload.get("tags", ())),
            None if actor is None else ActorRef(actor["kind"], actor["identity"]),
            tuple(sorted(payload["metadata"].items())),
            relations["reverses"],
            relations["reapplies"],
            relations["compensates"],
            None
            if conflict is None
            else ConflictDetail(conflict["target_id"], conflict["expected_version"], conflict["actual_version"]),
            None if exception is None else ExceptionSummary(exception["type_name"], exception["message"]),
        )


class ActionOutcomeSink(Protocol):
    """A synchronous consumer of retention-safe causal events."""

    def accept(self, outcome: CausalEventSnapshot) -> None: ...


@dataclass(frozen=True, slots=True)
class DurableOutcomePolicy:
    """The privacy, storage, and retention declaration for an encoded outcome sink."""

    redaction: RedactionPolicy = DEFAULT_REDACTION
    value_serialization: str = "summaries-only"
    actor_privacy: str = "application-controlled"
    encryption: str = "host-responsibility"
    retention: str = "host-defined"


class DurableOutcomeSink:
    """Encode committed/rolled-back outcomes for a host store. Calling :meth:`close` unregisters it."""

    def __init__(
        self,
        append: Callable[[bytes], None],
        *,
        policy: DurableOutcomePolicy | None = None,
        codec: ActionOutcomeCodec | None = None,
    ) -> None:
        self.append = append
        self.policy = DurableOutcomePolicy() if policy is None else policy
        self.codec = codec or ActionOutcomeCodec()
        self.closed = False
        add_action_outcome_sink(self, policy=self.policy.redaction)

    def accept(self, outcome: CausalEventSnapshot) -> None:
        if isinstance(outcome, ActionOutcomeSnapshot):
            self.append(self.codec.encode(outcome))

    def close(self) -> None:
        self.closed = True
        remove_action_outcome_sink(self)


class ActionLedger:
    """A bounded diagnostic ledger. Calling :meth:`close` ends its sink registration."""

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            message = "an action ledger needs room for at least one outcome"
            raise ValueError(message)
        self._events: deque[CausalEventSnapshot] = deque(maxlen=limit)

    def accept(self, outcome: CausalEventSnapshot) -> None:
        self._events.append(outcome)

    @property
    def events(self) -> tuple[CausalEventSnapshot, ...]:
        return tuple(self._events)

    @property
    def outcomes(self) -> tuple[ActionOutcomeSnapshot, ...]:
        return tuple(event for event in self._events if isinstance(event, ActionOutcomeSnapshot))

    def close(self) -> None:
        remove_action_outcome_sink(self)


_RUNTIME_ID = uuid.uuid4()
_sequence = 0


@dataclass(frozen=True, slots=True)
class _SinkRegistration:
    reference: weakref.ReferenceType[ActionOutcomeSink]
    policy: RedactionPolicy


_sinks: list[_SinkRegistration] = []
_CURRENT_ACTION: ContextVar[ActionContext | None] = ContextVar("squid_reactive_action", default=None)
_CURRENT_CAUSALITY: ContextVar[tuple[CausalRef, ActionId | None] | None] = ContextVar(
    "squid_reactive_causality", default=None
)
_aftermath_depth: ContextVar[int] = ContextVar("squid_reactive_aftermath_depth", default=0)


def next_commit_sequence() -> CommitSequence:
    global _sequence
    _sequence += 1
    return CommitSequence(_RUNTIME_ID, _sequence)


def current_action() -> ActionContext | None:
    """Return the live lexical action context, if any."""
    return _CURRENT_ACTION.get()


def current_causality() -> tuple[CausalRef, ActionId | None] | None:
    """Return the immutable cause/root pair installed for action, operation, or resource work."""
    return _CURRENT_CAUSALITY.get()


@contextmanager
def action_scope(context: ActionContext):
    """Install an action context until the lexical scope exits."""
    token = _CURRENT_ACTION.set(context)
    causal = _CURRENT_CAUSALITY.set((context.causal_ref(), context.root_action_id))
    try:
        yield context
    finally:
        _CURRENT_CAUSALITY.reset(causal)
        _CURRENT_ACTION.reset(token)


@contextmanager
def causal_scope(cause: CausalRef, root_action_id: ActionId | None):
    """Install detached immutable causality without granting live transaction authority."""
    token = _CURRENT_CAUSALITY.set((cause, root_action_id))
    try:
        yield
    finally:
        _CURRENT_CAUSALITY.reset(token)


def add_action_outcome_sink(sink: ActionOutcomeSink, *, policy: RedactionPolicy = DEFAULT_REDACTION) -> None:
    if not any(registration.reference() is sink for registration in _sinks):
        _sinks.append(_SinkRegistration(weakref.ref(sink), policy))


def remove_action_outcome_sink(sink: ActionOutcomeSink) -> None:
    _sinks[:] = [
        registration
        for registration in _sinks
        if (registered := registration.reference()) is not None and registered is not sink
    ]


def emit_outcome(outcome: ActionOutcome) -> None:
    if not _sinks:
        return
    live: list[_SinkRegistration] = []
    snapshots: dict[RedactionPolicy, ActionOutcomeSnapshot] = {}
    for registration in _sinks:
        sink = registration.reference()
        if sink is None:
            continue
        live.append(registration)
        try:
            snapshot = snapshots.get(registration.policy)
            if snapshot is None:
                snapshot = ActionOutcomeSnapshot.from_outcome(outcome, registration.policy)
                snapshots[registration.policy] = snapshot
            sink.accept(snapshot)
        except Exception as error:
            _log.exception("an action outcome sink failed")
            emit_aftermath_failure(
                outcome,
                "outcome_sink",
                type(sink).__qualname__,
                error,
            )
    _sinks[:] = live


def emit_causal_event(snapshot: CausalEventSnapshot) -> None:
    """Fan out one bounded diagnostic node without allowing a sink to veto runtime work."""
    live: list[_SinkRegistration] = []
    for registration in _sinks:
        sink = registration.reference()
        if sink is None:
            continue
        live.append(registration)
        try:
            sink.accept(snapshot)
        except Exception:
            _log.exception("an action outcome sink failed")
    _sinks[:] = live


def emit_aftermath_failure(outcome: ActionOutcome, stage: str, callback: str, error: BaseException) -> None:
    """Record a redacted post-outcome failure causally beneath its immutable outcome."""
    exception = DEFAULT_REDACTION.exception(ExceptionSummary.capture(error))
    assert exception is not None
    emit_causal_event(
        AftermathFailureSnapshot(
            str(uuid.uuid7()),
            str(outcome.context.root_action_id),
            outcome.context.causal_ref(),
            stage,
            callback,
            datetime.now(UTC),
            exception,
        )
    )


class Aftermath:
    """Authority to start fresh causal work after an outcome; the callback ends it."""

    def __init__(self, outcome: ActionOutcome) -> None:
        self.outcome = outcome

    @contextmanager
    def start_action(self, name: str, *, kind: ActionKind = ActionKind.RECOVERY):
        """Start a fresh causal action and transaction."""
        from squid_reactive.core import fresh_action_transaction

        context = ActionContext.create(
            name,
            kind=kind,
            cause=self.outcome.context.causal_ref(),
            root_action_id=self.outcome.context.root_action_id,
        )
        with fresh_action_transaction(action_context=context):
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
