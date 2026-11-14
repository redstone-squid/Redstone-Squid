"""Observe reactive work through redacted snapshots and scoped sinks.

Transaction results retain live change tokens for in-process history. Diagnostic
snapshots carry counts and causal identities instead, so sinks can retain them
without keeping reactive owners alive. Observer scopes apply redaction before
passing snapshots to a sink; applications own storage and serialization.
"""

from squid_reactivity.diagnostics.observers import (
    EventLedger,
    EventSink,
    copy_event_observers,
    emit_causal_event,
    emit_continuation_failure,
    emit_result,
    has_event_observers,
    observe_events,
)
from squid_reactivity.diagnostics.snapshots import (
    DEFAULT_REDACTION,
    CausalEventSnapshot,
    ContinuationFailureSnapshot,
    OperationEventSnapshot,
    RedactionPolicy,
    ResourceEventSnapshot,
    TransactionResultSnapshot,
)

__all__ = [
    "DEFAULT_REDACTION",
    "CausalEventSnapshot",
    "ContinuationFailureSnapshot",
    "EventLedger",
    "EventSink",
    "OperationEventSnapshot",
    "RedactionPolicy",
    "ResourceEventSnapshot",
    "TransactionResultSnapshot",
    "copy_event_observers",
    "emit_causal_event",
    "emit_continuation_failure",
    "emit_result",
    "has_event_observers",
    "observe_events",
]
