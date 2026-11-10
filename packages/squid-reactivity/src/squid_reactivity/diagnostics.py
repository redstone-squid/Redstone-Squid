"""Observe reactive work through redacted snapshots, scoped sinks, and portable encoding.

Transaction results retain live change tokens for in-process history. Diagnostic
snapshots carry counts and causal identities instead, so sinks can retain them
without keeping reactive owners alive. Observer scopes apply redaction before
passing snapshots to a sink; encoding and retention remain explicit choices.
"""

import json
import logging
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from squid_reactivity.actions import (
    ActionCommit,
    ActionRelation,
    ActionResult,
    ActionRollback,
    ActorRef,
    CausalRef,
    ChangeReport,
    CommitSequence,
    ConflictDetail,
    ExceptionReport,
)

_log = logging.getLogger(__name__)


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


class EventCodec:
    """Encode diagnostic events as JSON with one schema and an event discriminator.

    Decoding rejects unsupported schemas, malformed records, and oversized input.
    Encoding preserves the supplied event; observer scopes apply redaction before
    a sink receives it.
    """

    schema_version = 2
    max_encoded_bytes = 1_048_576
    _types: dict[str, type[CausalEventSnapshot]] = {
        "action": ActionResultSnapshot,
        "operation": OperationEventSnapshot,
        "resource": ResourceEventSnapshot,
        "continuation_failure": ContinuationFailureSnapshot,
    }

    def encode(self, event: CausalEventSnapshot) -> bytes:
        kind = next(kind for kind, event_type in self._types.items() if isinstance(event, event_type))
        return json.dumps(
            {"schema": self.schema_version, "event": kind, "data": asdict(event)},
            default=self._encode_value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def _encode_value(value: object) -> str | list[str]:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, frozenset):
            return sorted(value)
        message = f"unsupported event value {type(value).__name__}"
        raise TypeError(message)

    def decode(self, data: bytes) -> CausalEventSnapshot:
        if len(data) > self.max_encoded_bytes:
            message = "event exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            return self._decode(json.loads(data))
        except (UnicodeDecodeError, KeyError, TypeError, AttributeError, ValueError) as error:
            message = "event has a corrupt or unsupported schema"
            raise ValueError(message) from error

    def _decode(self, envelope: dict[str, Any]) -> CausalEventSnapshot:
        if envelope["schema"] != self.schema_version:
            message = "unsupported event schema"
            raise ValueError(message)
        event_type = self._types[envelope["event"]]
        payload = envelope["data"]
        payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
        if payload["cause"] is not None:
            payload["cause"] = CausalRef(**payload["cause"])
        if payload["exception"] is not None:
            payload["exception"] = ExceptionReport(**payload["exception"])
        if event_type is ActionResultSnapshot:
            payload["changes"] = ChangeReport(**payload["changes"])
            payload["tags"] = frozenset(payload["tags"])
            payload["metadata"] = tuple((key, value) for key, value in payload["metadata"])
            if payload["sequence"] is not None:
                sequence = payload["sequence"]
                payload["sequence"] = CommitSequence(uuid.UUID(sequence["runtime_id"]), sequence["value"])
            if payload["actor"] is not None:
                payload["actor"] = ActorRef(**payload["actor"])
            if payload["conflict"] is not None:
                payload["conflict"] = ConflictDetail(**payload["conflict"])
            if payload["relation"] is not None:
                relation = payload["relation"]
                if relation["kind"] not in ("reverses", "reapplies", "compensates"):
                    message = "unknown history relation"
                    raise ValueError(message)
                payload["relation"] = ActionRelation(relation["kind"], uuid.UUID(relation["action_id"]))
        elif event_type is ContinuationFailureSnapshot and (payload["cause"] is None or payload["exception"] is None):
            message = "a callback failure requires a cause and exception"
            raise ValueError(message)
        return event_type(**payload)


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


@dataclass(slots=True)
class _Observer:
    sink: EventSink | None
    redaction: RedactionPolicy


_observers: ContextVar[tuple[_Observer, ...]] = ContextVar("squid_reactivity_observers", default=())


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
