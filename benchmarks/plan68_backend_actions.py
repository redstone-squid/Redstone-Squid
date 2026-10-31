"""Measure long-lived action staging for the two experimental CRDT adapters."""

import json
import time
from collections.abc import Callable
from typing import Any

from squid_replicated.backends.loro import LoroTextEngine, LoroTextOperation
from squid_replicated.backends.pycrdt import PycrdtTextEngine, PycrdtTextOperation


def _measure(factory: Callable[[], Any], operation_type: type, actions: int) -> dict[str, int]:
    engine = factory()
    started = time.perf_counter_ns()
    for index in range(actions):
        branch = engine.branch()
        branch.apply(operation_type("insert", index, "x"))
        engine.apply(branch.prepare(engine.version()))
    elapsed = time.perf_counter_ns() - started
    result = {
        "actions": actions,
        "elapsed_ns": elapsed,
        "ns_per_action": elapsed // actions,
        "snapshot_bytes": len(engine.snapshot()),
        "state_vector_bytes": len(engine.version()),
        "update_bytes": len(engine.export_since()),
    }
    if isinstance(engine, LoroTextEngine):
        result["peer_count"] = len(engine.doc.oplog_vv.to_spans().inner())
    return result


def main() -> None:
    result: dict[str, list[dict[str, int]]] = {"loro": [], "pycrdt": []}
    for actions in (100, 1_000, 3_000):
        result["loro"].append(_measure(LoroTextEngine, LoroTextOperation, actions))
        result["pycrdt"].append(_measure(PycrdtTextEngine, PycrdtTextOperation, actions))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
