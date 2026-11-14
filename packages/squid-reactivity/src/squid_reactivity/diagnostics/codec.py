"""Encode portable diagnostic events and send encoded records to application storage."""

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from squid_reactivity.actions import (
    ActionRelation,
    ActorRef,
    CausalRef,
    ChangeReport,
    CommitSequence,
    ConflictDetail,
    ExceptionReport,
)
from squid_reactivity.diagnostics.snapshots import (
    ActionResultSnapshot,
    CausalEventSnapshot,
    ContinuationFailureSnapshot,
    OperationEventSnapshot,
    ResourceEventSnapshot,
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
