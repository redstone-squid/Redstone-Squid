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
import weakref
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
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


class ActionResultSink(Protocol):
    """A synchronous consumer of retention-safe causal events."""

    def accept(self, result: CausalEventSnapshot) -> None: ...


@dataclass(frozen=True, slots=True)
class DurableResultPolicy:
    """The privacy, storage, and retention declaration for an encoded result sink."""

    redaction: RedactionPolicy = DEFAULT_REDACTION
    value_serialization: str = "summaries-only"
    actor_privacy: str = "application-controlled"
    encryption: str = "host-responsibility"
    retention: str = "host-defined"


class DurableResultSink:
    """Encode committed/rolled-back results for a host store. Calling :meth:`close` unregisters it."""

    def __init__(
        self,
        append: Callable[[bytes], None],
        *,
        policy: DurableResultPolicy | None = None,
        codec: ActionResultCodec | None = None,
    ) -> None:
        self.append = append
        self.policy = DurableResultPolicy() if policy is None else policy
        self.codec = codec or ActionResultCodec()
        self.closed = False
        add_action_result_sink(self, policy=self.policy.redaction)

    def accept(self, result: CausalEventSnapshot) -> None:
        if isinstance(result, ActionResultSnapshot):
            self.append(self.codec.encode(result))

    def close(self) -> None:
        self.closed = True
        remove_action_result_sink(self)


class ActionLedger:
    """Keep the most recent diagnostic events, up to the configured limit.

    Register this ledger with add_action_result_sink or action_result_sink to
    receive events. Calling close removes that registration.
    """

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            message = "an action ledger needs room for at least one result"
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

    def close(self) -> None:
        remove_action_result_sink(self)


_RUNTIME_ID = uuid.uuid4()
_sequence = 0


@dataclass(frozen=True, slots=True)
class _SinkRegistration:
    reference: weakref.ReferenceType[ActionResultSink]
    policy: RedactionPolicy


_sinks: list[_SinkRegistration] = []
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


def add_action_result_sink(sink: ActionResultSink, *, policy: RedactionPolicy = DEFAULT_REDACTION) -> None:
    """Register a process-wide observer of committed and rolled-back actions.

    The sink is held weakly and observes redacted snapshots under `policy`. Registering an
    already-registered sink is a no-op and keeps its original policy. For an observer with a
    narrower life than the process, prefer :func:`action_result_sink`, which cannot leak a
    registration.
    """
    if not any(registration.reference() is sink for registration in _sinks):
        _sinks.append(_SinkRegistration(weakref.ref(sink), policy))


def remove_action_result_sink(sink: ActionResultSink) -> None:
    """Unregister a sink; unknown sinks are ignored."""
    _sinks[:] = [
        registration
        for registration in _sinks
        if (registered := registration.reference()) is not None and registered is not sink
    ]


@contextmanager
def action_result_sink(
    sink: ActionResultSink, *, policy: RedactionPolicy = DEFAULT_REDACTION
) -> Iterator[ActionResultSink]:
    """Register a sink until the with block exits.

    The scoped form of :func:`add_action_result_sink`, for observers -- a test collector, a
    bounded diagnostic capture -- whose life is a block rather than the process.
    """
    add_action_result_sink(sink, policy=policy)
    try:
        yield sink
    finally:
        remove_action_result_sink(sink)


def emit_result(result: ActionResult) -> None:
    if not _sinks:
        return
    live: list[_SinkRegistration] = []
    snapshots: dict[RedactionPolicy, ActionResultSnapshot] = {}
    failures: list[tuple[ActionResultSink, Exception]] = []
    for registration in _sinks:
        sink = registration.reference()
        if sink is None:
            continue
        live.append(registration)
        try:
            snapshot = snapshots.get(registration.policy)
            if snapshot is None:
                snapshot = ActionResultSnapshot.from_result(result, registration.policy)
                snapshots[registration.policy] = snapshot
            sink.accept(snapshot)
        except Exception as error:
            _log.exception("an action result sink failed")
            failures.append((sink, error))
    _sinks[:] = live
    for sink, error in failures:
        emit_continuation_failure(result, "result_sink", type(sink).__qualname__, error)


def emit_causal_event(snapshot: CausalEventSnapshot) -> None:
    """Send an event to live sinks, logging sink failures and continuing delivery."""
    live: list[_SinkRegistration] = []
    for registration in _sinks:
        sink = registration.reference()
        if sink is None:
            continue
        live.append(registration)
        try:
            sink.accept(snapshot)
        except Exception:
            _log.exception("an action result sink failed")
    _sinks[:] = live


def emit_continuation_failure(result: ActionResult, stage: str, callback: str, error: BaseException) -> None:
    """Report a callback failure linked to its action, with the message redacted."""
    exception = DEFAULT_REDACTION.redact_exception(ExceptionReport.capture(error))
    assert exception is not None
    emit_causal_event(
        ContinuationFailureSnapshot(
            str(uuid.uuid7()),
            str(result.context.root_action_id),
            result.context.causal_ref(),
            stage,
            callback,
            datetime.now(UTC),
            exception,
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
