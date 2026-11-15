"""Deliver diagnostic events within execution scopes, isolating sink failures.

Registrations inherited by child tasks expire with their originating scope.
Each sink receives one snapshot under its innermost active redaction policy.
"""

import logging
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, override

from squid_reactivity.diagnostics._snapshots import (
    DEFAULT_REDACTION,
    CausalEventSnapshot,
    ContinuationFailureSnapshot,
    RedactionPolicy,
    TransactionResultSnapshot,
    TransactionValidationSnapshot,
)
from squid_reactivity.transactions._context import (
    ExceptionReport,
)
from squid_reactivity.transactions._results import (
    TransactionCommit,
    TransactionResult,
    TransactionRollback,
)

_log = logging.getLogger(__name__)


class EventSink(Protocol):
    """Accept diagnostic snapshots synchronously without retaining live reactive objects."""

    __slots__ = ()

    def accept(self, event: CausalEventSnapshot, /) -> None: ...


class EventLedger(EventSink):
    """Keep the most recent diagnostic events, up to the configured limit.

    Pass this ledger to observe_events to collect events within an execution scope.
    """

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            message = "an event ledger needs room for at least one event"
            raise ValueError(message)
        self._events: deque[CausalEventSnapshot] = deque(maxlen=limit)

    @property
    def events(self) -> tuple[CausalEventSnapshot, ...]:
        return tuple(self._events)

    @property
    def results(self) -> tuple[TransactionResultSnapshot, ...]:
        return tuple(event for event in self._events if isinstance(event, TransactionResultSnapshot))

    @override
    def accept(self, result: CausalEventSnapshot) -> None:
        self._events.append(result)


@dataclass(slots=True)
class _Observer:
    sink: EventSink | None
    redaction: RedactionPolicy
    include_validation: bool


_EVENT_OBSERVERS: ContextVar[tuple[_Observer, ...]] = ContextVar("squid_reactivity_observers", default=())
"""Diagnostic registrations for this scope, inherited by spawned tasks.

Each registration is disabled when its owning scope exits, including in child contexts.
Schedulers copying registrations into background work preserve that lifetime, not just the sink.
"""


@contextmanager
def observe_events(
    sink: EventSink,
    *,
    redaction: RedactionPolicy = DEFAULT_REDACTION,
    include_validation: bool = False,
) -> Iterator[EventSink]:
    """Observe work in this execution scope and tasks started within it.

    Exiting disables delivery even in child tasks that inherited the scope. The
    scope owns the registration, not the sink. Nested scopes for the same sink
    deliver once using the innermost active scope's options. Set include_validation
    to receive effective cell preconditions sampled at commit-time validation.
    """
    registration = _Observer(sink, redaction, include_validation)
    token = _EVENT_OBSERVERS.set((*_EVENT_OBSERVERS.get(), registration))
    try:
        yield sink
    finally:
        registration.sink = None
        _EVENT_OBSERVERS.reset(token)


def copy_event_observers(base: Context) -> Context:
    """Add current diagnostic registrations to a copy of another execution context.

    Schedulers can use this when starting work with a clean context. Registrations
    still expire when their original observation scopes exit.
    """
    context = base.copy()
    inherited = context.get(_EVENT_OBSERVERS, ())
    registrations = {id(observer): observer for observer in (*inherited, *_EVENT_OBSERVERS.get())}
    context.run(_EVENT_OBSERVERS.set, tuple(registrations.values()))
    return context


def has_event_observers() -> bool:
    """Whether this execution scope has any active diagnostic observers."""
    for observer in _EVENT_OBSERVERS.get():  # noqa: SIM110 - Avoid allocating a generator for each event.
        if observer.sink is not None:
            return True
    return False


def has_validation_observers() -> bool:
    """Whether a sink has opted into transaction validation details."""
    observers = _EVENT_OBSERVERS.get()
    if not observers:
        return False
    seen: set[int] = set()
    for observer in reversed(observers):
        if observer.sink is None or id(observer.sink) in seen:
            continue
        seen.add(id(observer.sink))
        if observer.include_validation:
            return True
    return False


def emit_result(result: TransactionResult) -> None:
    """Deliver a redacted transaction summary and report failures from result sinks."""
    _deliver(result)


def emit_causal_event(snapshot: CausalEventSnapshot) -> None:
    """Apply each observer's policy and isolate failures during delivery."""
    _deliver(snapshot)


def emit_continuation_failure(result: TransactionResult, stage: str, callback: str, error: BaseException) -> None:
    """Report a callback failure linked to its transaction outcome."""
    if not has_event_observers():
        return
    emit_causal_event(
        ContinuationFailureSnapshot(
            str(uuid.uuid7()),
            str(result.context.root_transaction_id),
            result.context.causal_ref(),
            stage,
            callback,
            datetime.now(UTC),
            ExceptionReport.capture(error),
        )
    )


def _deliver(event: TransactionResult | CausalEventSnapshot) -> None:
    observers = _EVENT_OBSERVERS.get()
    if not observers:
        return
    seen: set[int] = set()
    snapshots: dict[RedactionPolicy, CausalEventSnapshot] = {}
    failures: list[tuple[str, Exception]] = []
    for observer in reversed(observers):
        sink = observer.sink
        if sink is None or id(sink) in seen:
            continue
        seen.add(id(sink))
        if isinstance(event, TransactionValidationSnapshot) and not observer.include_validation:
            continue
        try:
            snapshot = snapshots.get(observer.redaction)
            if snapshot is None:
                snapshot = (
                    TransactionResultSnapshot.from_result(event, observer.redaction)
                    if isinstance(event, (TransactionCommit, TransactionRollback))
                    else observer.redaction.redact(event)
                )
                snapshots[observer.redaction] = snapshot
            sink.accept(snapshot)
        except Exception as error:
            _log.exception("a diagnostic event sink failed")
            failures.append((type(sink).__qualname__, error))
    # Only transaction-result delivery generates failure events. A failed delivery of
    # those diagnostics is logged without producing another diagnostic recursively.
    if isinstance(event, (TransactionCommit, TransactionRollback)):
        for callback, error in failures:
            emit_continuation_failure(event, "result_sink", callback, error)
