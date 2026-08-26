"""Stable JSON export for immutable runtime profiles."""

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from squid_ui.profiling.model import RuntimeSnapshot, SpanId, TraceId


def _json_value(value: object) -> Any:
    if isinstance(value, (TraceId, SpanId)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    message = "unsupported JSON value type: " + repr(type(value))
    raise TypeError(message)


def snapshot_json(snapshot: RuntimeSnapshot, *, indent: int | None = None) -> str:
    """Serialize a runtime snapshot without consulting live profiler state."""
    if not isinstance(snapshot, RuntimeSnapshot):
        message = "snapshot_json expects RuntimeSnapshot"
        raise TypeError(message)
    separators = None if indent is not None else (",", ":")
    return json.dumps(_json_value(snapshot), indent=indent, separators=separators)
