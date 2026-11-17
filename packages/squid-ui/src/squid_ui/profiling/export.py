"""Stable JSON export for immutable runtime profiles."""

import json

from pydantic import TypeAdapter

from squid_ui.profiling.model import RuntimeSnapshot

_SNAPSHOT_WIRE = TypeAdapter(RuntimeSnapshot)


def snapshot_json(snapshot: RuntimeSnapshot, *, indent: int | None = None) -> str:
    """Serialize a runtime snapshot without consulting live profiler state."""
    if not isinstance(snapshot, RuntimeSnapshot):
        message = "snapshot_json expects RuntimeSnapshot"
        raise TypeError(message)
    separators = None if indent is not None else (",", ":")
    return json.dumps(_SNAPSHOT_WIRE.dump_python(snapshot, mode="json"), indent=indent, separators=separators)
