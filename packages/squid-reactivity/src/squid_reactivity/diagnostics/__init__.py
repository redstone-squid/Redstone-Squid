"""Observe reactive work through redacted snapshots, scoped sinks, and portable encoding.

Transaction results retain live change tokens for in-process history. Diagnostic
snapshots carry counts and causal identities instead, so sinks can retain them
without keeping reactive owners alive. Observer scopes apply redaction before
passing snapshots to a sink; encoding and retention remain explicit choices.
"""

from squid_reactivity.diagnostics.codec import (
    EncodedEventSink,
    EventCodec,
)
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
    ActionResultSnapshot,
    CausalEventSnapshot,
    ContinuationFailureSnapshot,
    OperationEventSnapshot,
    RedactionPolicy,
    ResourceEventSnapshot,
)

__all__ = [
    "DEFAULT_REDACTION",
    "ActionResultSnapshot",
    "CausalEventSnapshot",
    "ContinuationFailureSnapshot",
    "EncodedEventSink",
    "EventCodec",
    "EventLedger",
    "EventSink",
    "OperationEventSnapshot",
    "RedactionPolicy",
    "ResourceEventSnapshot",
    "copy_event_observers",
    "emit_causal_event",
    "emit_continuation_failure",
    "emit_result",
    "has_event_observers",
    "observe_events",
]
