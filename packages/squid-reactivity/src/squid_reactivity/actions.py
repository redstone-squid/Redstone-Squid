"""Describe why a reactive transaction ran and what it committed or rolled back.

ActionContext gives the transaction an identity and links it to the event that
caused it. A completed transaction produces an ActionCommit or ActionRollback;
history can use the commit's reversible changes, while diagnostic sinks receive
portable summaries. Operations and resource loads carry causal references so
their later results can still be traced to the initiating action.
"""

import json
import logging
import uuid
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import Context, ContextVar
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    # `core` imports this module at runtime, so the edge back has to stay type-only.
    from squid_reactivity.core import CellPatchSet

_log = logging.getLogger(__name__)

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


@dataclass(frozen=True, slots=True)
class ActionResultSnapshot:
    """A portable action summary containing change counts instead of cell values."""

    action_id: str
    root_action_id: str
    cause: CausalRef | None
    kind: str
    name: str
    terminal: str
    timestamp: datetime
    sequence: CommitSequence | None
    reason: str | None
    changes: ChangeReport
    tags: frozenset[str] = frozenset()
    actor: ActorRef | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    relation: ActionRelation | None = None
    conflict: ConflictDetail | None = None
    exception: ExceptionReport | None = None

    @classmethod
    def from_result(
        cls,
        result: ActionResult,
        policy: RedactionPolicy | None = None,
    ) -> ActionResultSnapshot:
        policy = DEFAULT_REDACTION if policy is None else policy
        context = result.context
        if isinstance(result, ActionCommit):
            terminal = "committed"
            timestamp = result.committed_at
            sequence = result.sequence
            reason = None
            changes = ChangeReport(len(result.patches), len(result.participant_changes))
            tags = result.tags
            conflict = None
            exception = None
        else:
            terminal = "rolled_back"
            timestamp = result.rolled_back_at
            sequence = None
            reason = result.reason.value
            changes = result.staged_report
            tags = frozenset()
            conflict = result.conflict
            exception = policy.redact_exception(result.exception)
        return cls(
            action_id=str(context.action_id),
            root_action_id=str(context.root_action_id),
            cause=context.cause,
            kind=context.kind.value,
            name=context.name,
            terminal=terminal,
            timestamp=timestamp,
            sequence=sequence,
            reason=reason,
            changes=changes,
            tags=tags,
            actor=context.actor if policy.include_actor else None,
            metadata=tuple(sorted(context.metadata.items())) if policy.include_metadata else (),
            relation=context.relation,
            conflict=conflict,
            exception=exception,
        )


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Explicit portable-result policy for metadata and exception messages."""

    include_actor: bool = True
    include_metadata: bool = False
    include_exception_messages: bool = False

    def redact_exception(self, report: ExceptionReport | None) -> ExceptionReport | None:
        if report is None or self.include_exception_messages:
            return report
        return ExceptionReport(report.type_name, "[redacted]")


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
    exception: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class ResourceEventSnapshot:
    """One start or terminal transition of a causally identified resource generation."""

    generation_id: str
    root_action_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class ContinuationFailureSnapshot:
    """A post-result failure that cannot alter the immutable action result."""

    failure_id: str
    root_action_id: str
    cause: CausalRef
    stage: str
    callback: str
    timestamp: datetime
    exception: ExceptionReport


type CausalEventSnapshot = (
    ActionResultSnapshot | OperationEventSnapshot | ResourceEventSnapshot | ContinuationFailureSnapshot
)


class ActionResultCodec:
    """Encode and decode action summaries as versioned JSON.

    Decoding rejects unsupported versions, malformed records, and inputs larger
    than max_encoded_bytes. Encoding preserves the supplied snapshot, so callers
    must apply any required redaction before passing it here.
    """

    schema_version = 1
    max_encoded_bytes = 1_048_576

    def encode(self, result: ActionResultSnapshot) -> bytes:
        payload = {
            "schema": self.schema_version,
            "action_id": result.action_id,
            "root_action_id": result.root_action_id,
            "cause": None if result.cause is None else {"kind": result.cause.kind, "identity": result.cause.identity},
            "kind": result.kind,
            "name": result.name,
            "terminal": result.terminal,
            "timestamp": result.timestamp.isoformat(),
            "sequence": None
            if result.sequence is None
            else {"runtime_id": str(result.sequence.runtime_id), "value": result.sequence.value},
            "reason": result.reason,
            "changes": {"cells": result.changes.cells, "participants": result.changes.participants},
            "tags": sorted(result.tags),
            "actor": None if result.actor is None else {"kind": result.actor.kind, "identity": result.actor.identity},
            "metadata": dict(result.metadata),
            "relations": {
                kind: str(result.relation.action_id)
                if result.relation is not None and result.relation.kind == kind
                else None
                for kind in ("reverses", "reapplies", "compensates")
            },
            "conflict": None
            if result.conflict is None
            else {
                "target_id": result.conflict.target_id,
                "expected_version": result.conflict.expected_version,
                "actual_version": result.conflict.actual_version,
            },
            "exception": None
            if result.exception is None
            else {"type_name": result.exception.type_name, "message": result.exception.message},
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def decode(self, data: bytes) -> ActionResultSnapshot:
        if len(data) > self.max_encoded_bytes:
            message = "action result exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, ValueError) as error:
            message = "action result has a corrupt schema"
            raise ValueError(message) from error
        if not isinstance(payload, dict):
            message = "action result has a corrupt schema"
            raise ValueError(message)  # noqa: TRY004 - malformed encoded values share one public error type.
        if payload.get("schema") != self.schema_version:
            message = f"unsupported action result schema {payload.get('schema')!r}"
            raise ValueError(message)
        try:
            return self._decode(payload)
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            message = "action result has a corrupt schema"
            raise ValueError(message) from error

    def _decode(self, payload: dict[str, Any]) -> ActionResultSnapshot:
        cause = payload["cause"]
        sequence = payload["sequence"]
        actor = payload["actor"]
        relations = payload["relations"]
        relation = None
        kinds: tuple[Literal["reverses", "reapplies", "compensates"], ...] = ("reverses", "reapplies", "compensates")
        for kind in kinds:
            if relations[kind] is not None:
                if relation is not None:
                    message = "an action can have only one history relation"
                    raise ValueError(message)
                relation = ActionRelation(kind, uuid.UUID(relations[kind]))
        conflict = payload["conflict"]
        exception = payload["exception"]
        return ActionResultSnapshot(
            payload["action_id"],
            payload["root_action_id"],
            None if cause is None else CausalRef(cause["kind"], cause["identity"]),
            payload["kind"],
            payload["name"],
            payload["terminal"],
            datetime.fromisoformat(payload["timestamp"]),
            None if sequence is None else CommitSequence(uuid.UUID(sequence["runtime_id"]), sequence["value"]),
            payload["reason"],
            ChangeReport(payload["changes"]["cells"], payload["changes"]["participants"]),
            frozenset(payload.get("tags", ())),
            None if actor is None else ActorRef(actor["kind"], actor["identity"]),
            tuple(sorted(payload["metadata"].items())),
            relation,
            None
            if conflict is None
            else ConflictDetail(conflict["target_id"], conflict["expected_version"], conflict["actual_version"]),
            None if exception is None else ExceptionReport(exception["type_name"], exception["message"]),
        )


class EventCodec:
    """Encode all diagnostic event variants in a versioned JSON envelope.

    Action payloads retain the ActionResultCodec format. Encoding preserves the
    supplied event; observer scopes apply redaction before a sink receives it.
    """

    schema_version = 1
    max_encoded_bytes = 1_048_576

    def encode(self, event: CausalEventSnapshot) -> bytes:
        if isinstance(event, ActionResultSnapshot):
            kind = "action"
            payload = json.loads(ActionResultCodec().encode(event))
        else:
            kind = {
                OperationEventSnapshot: "operation",
                ResourceEventSnapshot: "resource",
                ContinuationFailureSnapshot: "continuation_failure",
            }[type(event)]
            payload = asdict(event)
            payload["timestamp"] = event.timestamp.isoformat()
        return json.dumps(
            {"schema": self.schema_version, "event": kind, "data": payload}, sort_keys=True, separators=(",", ":")
        ).encode()

    def decode(self, data: bytes) -> CausalEventSnapshot:
        if len(data) > self.max_encoded_bytes:
            message = "event exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            envelope = json.loads(data)
            if envelope["schema"] != self.schema_version:
                message = "unsupported event schema"
                raise ValueError(message)  # noqa: TRY301 - unify malformed input errors below.
            kind, payload = envelope["event"], envelope["data"]
            if kind == "action":
                return ActionResultCodec().decode(json.dumps(payload).encode())
            timestamp = datetime.fromisoformat(payload["timestamp"])
            cause = payload["cause"]
            cause = None if cause is None else CausalRef(cause["kind"], cause["identity"])
            report = payload["exception"]
            report = None if report is None else ExceptionReport(report["type_name"], report["message"])
            if kind == "operation":
                return OperationEventSnapshot(
                    payload["execution_id"],
                    payload["root_action_id"],
                    cause,
                    payload["name"],
                    payload["status"],
                    timestamp,
                    report,
                )
            if kind == "resource":
                return ResourceEventSnapshot(
                    payload["generation_id"],
                    payload["root_action_id"],
                    cause,
                    payload["name"],
                    payload["status"],
                    timestamp,
                    report,
                )
            if kind == "continuation_failure" and cause is not None and report is not None:
                return ContinuationFailureSnapshot(
                    payload["failure_id"],
                    payload["root_action_id"],
                    cause,
                    payload["stage"],
                    payload["callback"],
                    timestamp,
                    report,
                )
            message = "unknown or incomplete event variant"
            raise ValueError(message)  # noqa: TRY301 - unify malformed input errors below.
        except (UnicodeDecodeError, KeyError, TypeError, AttributeError, ValueError) as error:
            message = "event has a corrupt or unsupported schema"
            raise ValueError(message) from error


class EventSink(Protocol):
    """A synchronous consumer of retention-safe diagnostic events."""

    def accept(self, event: CausalEventSnapshot, /) -> None: ...


class EncodedEventSink:
    """Encode events and pass them to application-owned storage.

    Register with observe_events to receive events. The application owns the
    append callback and its storage, retention, and durability guarantees.
    """

    def __init__(self, codec: EventCodec, append: Callable[[bytes], None]) -> None:
        self.codec = codec
        self.append = append

    def accept(self, event: CausalEventSnapshot) -> None:
        self.append(self.codec.encode(event))


class EventLedger:
    """Keep the most recent diagnostic events, up to the configured limit.

    Pass this ledger to observe_events to collect events within an execution scope.
    """

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            message = "an event ledger needs room for at least one event"
            raise ValueError(message)
        self._events: deque[CausalEventSnapshot] = deque(maxlen=limit)

    def accept(self, result: CausalEventSnapshot) -> None:
        self._events.append(result)

    @property
    def events(self) -> tuple[CausalEventSnapshot, ...]:
        return tuple(self._events)

    @property
    def results(self) -> tuple[ActionResultSnapshot, ...]:
        return tuple(event for event in self._events if isinstance(event, ActionResultSnapshot))


_RUNTIME_ID = uuid.uuid4()
_sequence = 0


@dataclass(slots=True)
class _Observer:
    sink: EventSink | None
    redaction: RedactionPolicy


_observers: ContextVar[tuple[_Observer, ...]] = ContextVar("squid_reactivity_observers", default=())
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
def observe_events(sink: EventSink, *, redaction: RedactionPolicy = DEFAULT_REDACTION) -> Iterator[EventSink]:
    """Observe work in this execution scope and tasks started within it.

    Exiting disables delivery even in child tasks that inherited the scope. The
    scope owns the registration, not the sink. Nested scopes for the same sink
    deliver once using the innermost active scope's policy.
    """
    registration = _Observer(sink, redaction)
    token = _observers.set((*_observers.get(), registration))
    try:
        yield sink
    finally:
        registration.sink = None
        _observers.reset(token)


def copy_event_observers(base: Context) -> Context:
    """Copy a host context with the submitting scope's diagnostic registrations.

    Schedulers that discard caller-local state can use this for submitted work.
    Registrations keep their original lifetimes; this does not extend a scope.
    """
    context = base.copy()
    inherited = context.get(_observers, ())
    registrations = {id(observer): observer for observer in (*inherited, *_observers.get())}
    context.run(_observers.set, tuple(registrations.values()))
    return context


def has_event_observers() -> bool:
    """Whether this execution scope has any active diagnostic observers."""
    return any(observer.sink is not None for observer in _observers.get())


def _redact(snapshot: CausalEventSnapshot, policy: RedactionPolicy) -> CausalEventSnapshot:
    if isinstance(snapshot, ActionResultSnapshot):
        return replace(
            snapshot,
            actor=snapshot.actor if policy.include_actor else None,
            metadata=snapshot.metadata if policy.include_metadata else (),
            exception=policy.redact_exception(snapshot.exception),
        )
    exception = policy.redact_exception(snapshot.exception)
    if isinstance(snapshot, ContinuationFailureSnapshot):
        assert exception is not None
        return replace(snapshot, exception=exception)
    return replace(snapshot, exception=exception)


def _deliver(event: ActionResult | CausalEventSnapshot) -> None:
    seen: set[int] = set()
    snapshots: dict[RedactionPolicy, CausalEventSnapshot] = {}
    failures: list[tuple[str, Exception]] = []
    for observer in reversed(_observers.get()):
        sink = observer.sink
        if sink is None or id(sink) in seen:
            continue
        seen.add(id(sink))
        try:
            snapshot = snapshots.get(observer.redaction)
            if snapshot is None:
                snapshot = (
                    ActionResultSnapshot.from_result(event, observer.redaction)
                    if isinstance(event, (ActionCommit, ActionRollback))
                    else _redact(event, observer.redaction)
                )
                snapshots[observer.redaction] = snapshot
            sink.accept(snapshot)
        except Exception as error:
            _log.exception("a diagnostic event sink failed")
            failures.append((type(sink).__qualname__, error))
    # Only action-result delivery generates failure events. A failed delivery of
    # those diagnostics is logged without producing another diagnostic recursively.
    if isinstance(event, (ActionCommit, ActionRollback)):
        for callback, error in failures:
            emit_continuation_failure(event, "result_sink", callback, error)


def emit_result(result: ActionResult) -> None:
    _deliver(result)


def emit_causal_event(snapshot: CausalEventSnapshot) -> None:
    """Apply each observer's policy and isolate failures during delivery."""
    _deliver(snapshot)


def emit_continuation_failure(result: ActionResult, stage: str, callback: str, error: BaseException) -> None:
    """Report a callback failure linked to its action."""
    if not has_event_observers():
        return
    emit_causal_event(
        ContinuationFailureSnapshot(
            str(uuid.uuid7()),
            str(result.context.root_action_id),
            result.context.causal_ref(),
            stage,
            callback,
            datetime.now(UTC),
            ExceptionReport.capture(error),
        )
    )


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
