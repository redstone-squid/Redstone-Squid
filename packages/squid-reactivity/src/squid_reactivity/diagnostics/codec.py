"""Encode portable diagnostic events and send encoded records to application storage."""

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

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

    Decoding checks record fields and value types and rejects unsupported schemas
    and oversized input.
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
        except (TypeError, ValueError, RecursionError) as error:
            message = "event has a corrupt or unsupported schema"
            raise ValueError(message) from error

    def _decode(self, value: object) -> CausalEventSnapshot:
        envelope = _record(value, {"schema", "event", "data"})
        if _integer(envelope["schema"]) != self.schema_version:
            message = "unsupported event schema"
            raise ValueError(message)
        kind = _string(envelope["event"])
        if kind == "action":
            return _action(envelope["data"])
        common = {"root_action_id", "cause", "timestamp", "exception"}
        if kind == "continuation_failure":
            payload = _record(envelope["data"], common | {"failure_id", "stage", "callback"})
            return ContinuationFailureSnapshot(
                failure_id=_string(payload["failure_id"]),
                root_action_id=_string(payload["root_action_id"]),
                cause=_cause(payload["cause"]),
                stage=_string(payload["stage"]),
                callback=_string(payload["callback"]),
                timestamp=datetime.fromisoformat(_string(payload["timestamp"])),
                exception=_exception(payload["exception"]),
            )
        if kind == "operation":
            event_type = OperationEventSnapshot
            identity = "execution_id"
        elif kind == "resource":
            event_type = ResourceEventSnapshot
            identity = "generation_id"
        else:
            message = "unknown diagnostic event"
            raise ValueError(message)
        payload = _record(envelope["data"], common | {identity, "name", "status"})
        return event_type(
            _string(payload[identity]),
            root_action_id=_optional(payload["root_action_id"], _string),
            cause=_optional(payload["cause"], _cause),
            name=_string(payload["name"]),
            status=_string(payload["status"]),
            timestamp=datetime.fromisoformat(_string(payload["timestamp"])),
            exception=_optional(payload["exception"], _exception),
        )


def _record(value: object, fields: set[str]) -> dict[str, object]:
    """Require the exact fields of a schema record before decoding its values."""
    if not isinstance(value, dict) or value.keys() != fields:
        message = "unexpected record fields"
        raise ValueError(message)
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        message = "expected a string"
        raise TypeError(message)
    return value


def _integer(value: object) -> int:
    # JSON booleans are Python ints too, but are not valid counts or versions.
    if not isinstance(value, int) or isinstance(value, bool):
        message = "expected an integer"
        raise TypeError(message)
    return value


def _optional[ValueT](value: object, decode: Callable[[object], ValueT]) -> ValueT | None:
    return None if value is None else decode(value)


def _cause(value: object) -> CausalRef:
    record = _record(value, {"kind", "identity"})
    return CausalRef(_string(record["kind"]), _string(record["identity"]))


def _actor(value: object) -> ActorRef:
    record = _record(value, {"kind", "identity"})
    return ActorRef(_string(record["kind"]), _string(record["identity"]))


def _exception(value: object) -> ExceptionReport:
    record = _record(value, {"type_name", "message"})
    return ExceptionReport(_string(record["type_name"]), _string(record["message"]))


def _changes(value: object) -> ChangeReport:
    record = _record(value, {"cells", "participants"})
    return ChangeReport(_integer(record["cells"]), _integer(record["participants"]))


def _sequence(value: object) -> CommitSequence:
    record = _record(value, {"runtime_id", "value"})
    return CommitSequence(uuid.UUID(_string(record["runtime_id"])), _integer(record["value"]))


def _conflict(value: object) -> ConflictDetail:
    record = _record(value, {"target_id", "expected_version", "actual_version"})
    return ConflictDetail(
        _string(record["target_id"]), _integer(record["expected_version"]), _integer(record["actual_version"])
    )


def _relation(value: object) -> ActionRelation:
    record = _record(value, {"kind", "action_id"})
    kind = _string(record["kind"])
    if kind not in ("reverses", "reapplies", "compensates"):
        message = "unknown history relation"
        raise ValueError(message)
    return ActionRelation(kind, uuid.UUID(_string(record["action_id"])))


def _tags(value: object) -> frozenset[str]:
    if not isinstance(value, list):
        message = "expected a tag array"
        raise TypeError(message)
    return frozenset(_string(tag) for tag in value)


def _metadata(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        message = "expected a metadata array"
        raise TypeError(message)
    pairs: list[tuple[str, str]] = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            message = "expected a metadata key/value pair"
            raise ValueError(message)
        pairs.append((_string(pair[0]), _string(pair[1])))
    return tuple(pairs)


def _action(value: object) -> ActionResultSnapshot:
    payload = _record(
        value,
        {
            "action_id",
            "root_action_id",
            "cause",
            "kind",
            "name",
            "terminal",
            "timestamp",
            "sequence",
            "reason",
            "changes",
            "tags",
            "actor",
            "metadata",
            "relation",
            "conflict",
            "exception",
        },
    )
    return ActionResultSnapshot(
        action_id=_string(payload["action_id"]),
        root_action_id=_string(payload["root_action_id"]),
        cause=_optional(payload["cause"], _cause),
        kind=_string(payload["kind"]),
        name=_string(payload["name"]),
        terminal=_string(payload["terminal"]),
        timestamp=datetime.fromisoformat(_string(payload["timestamp"])),
        sequence=_optional(payload["sequence"], _sequence),
        reason=_optional(payload["reason"], _string),
        changes=_changes(payload["changes"]),
        tags=_tags(payload["tags"]),
        actor=_optional(payload["actor"], _actor),
        metadata=_metadata(payload["metadata"]),
        relation=_optional(payload["relation"], _relation),
        conflict=_optional(payload["conflict"], _conflict),
        exception=_optional(payload["exception"], _exception),
    )


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
