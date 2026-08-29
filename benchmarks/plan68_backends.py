"""Focused representative measurements for the two experimental Plan 68 text adapters."""

import json
import statistics
import time
from collections.abc import Callable
from typing import Any

from squid_replicated.backends.loro import LoroTextEngine, LoroTextOperation
from squid_replicated.backends.pycrdt import PycrdtTextEngine, PycrdtTextOperation


def _median(operation: Callable[[], Any], iterations: int) -> int:
    samples: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return int(statistics.median(samples))


def _measure(factory: Callable[[], Any], operation_type: type, size: int) -> dict[str, int]:
    engine = factory()
    seed = engine.branch()
    seed.apply(operation_type("insert", 0, "x" * size))
    seed_token = engine.apply(seed.prepare(engine.version()))
    assert seed_token is not None
    iterations = 20 if size <= 10_000 else 7
    stage_samples: list[int] = []
    apply_samples: list[int] = []
    last_token = seed_token
    for _ in range(iterations):
        started = time.perf_counter_ns()
        branch = engine.branch()
        branch.apply(operation_type("insert", len(engine.snapshot()), "y"))
        prepared = branch.prepare(engine.version())
        stage_samples.append(time.perf_counter_ns() - started)
        started = time.perf_counter_ns()
        last_token = engine.apply(prepared)
        apply_samples.append(time.perf_counter_ns() - started)
    assert last_token is not None
    update = engine.export_since()

    def import_fresh() -> None:
        target = factory()
        target.apply(target.prepare_remote(update))

    encoded_token = last_token.encode()
    decode = type(last_token).decode
    return {
        "apply_ns": int(statistics.median(apply_samples)),
        "export_bytes": len(update),
        "import_fresh_ns": _median(import_fresh, max(3, iterations // 2)),
        "inverse_plan_ns": _median(lambda: engine.plan_inverse(decode(encoded_token)), iterations),
        "snapshot_ns": _median(engine.snapshot, iterations),
        "stage_prepare_ns": int(statistics.median(stage_samples)),
        "token_bytes": len(encoded_token),
    }


def main() -> None:
    result: dict[str, dict[str, dict[str, int]]] = {"loro": {}, "pycrdt": {}}
    for size in (1_000, 10_000, 50_000):
        result["loro"][str(size)] = _measure(LoroTextEngine, LoroTextOperation, size)
        result["pycrdt"][str(size)] = _measure(PycrdtTextEngine, PycrdtTextOperation, size)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
