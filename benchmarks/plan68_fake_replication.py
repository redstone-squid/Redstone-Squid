"""Focused scale baseline for the narrowed deterministic counter/tagged-set adapter."""

import json
import statistics
import time
from collections.abc import Callable
from typing import Any

from squid_replicated.fake import FakeEngine


def _median(operation: Callable[[], Any], iterations: int) -> int:
    samples: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append(time.perf_counter_ns() - started)
    return int(statistics.median(samples))


def _measure(operation_count: int) -> dict[str, int]:
    engine = FakeEngine("source")
    seed = engine.branch()
    for index in range(operation_count):
        seed.apply(engine.operation("increment", "votes", 1))
        seed.apply(engine.operation("add", "tags", f"tag-{index}"))
    engine.apply(seed.prepare(seed.base))
    iterations = 30 if operation_count <= 1_000 else 7

    stage_samples: list[int] = []
    apply_samples: list[int] = []
    last_prepared = None
    for index in range(iterations):
        started = time.perf_counter_ns()
        branch = engine.branch()
        branch.apply(engine.operation("increment", "votes", 1))
        branch.apply(engine.operation("add", "tags", f"later-{index}"))
        last_prepared = branch.prepare(branch.base)
        stage_samples.append(time.perf_counter_ns() - started)
        started = time.perf_counter_ns()
        engine.apply(last_prepared)
        apply_samples.append(time.perf_counter_ns() - started)
    assert last_prepared is not None
    update = engine.export_since()

    def import_fresh() -> None:
        target = FakeEngine("target")
        target.apply(target.prepare_remote(update))

    token = engine.encode_token(last_prepared.operations)
    return {
        "apply_ns": int(statistics.median(apply_samples)),
        "export_bytes": len(update),
        "import_fresh_ns": _median(import_fresh, max(3, iterations // 3)),
        "snapshot_ns": _median(engine.snapshot, iterations),
        "stage_prepare_ns": int(statistics.median(stage_samples)),
        "token_bytes": len(token),
    }


def main() -> None:
    result = {str(size): _measure(size) for size in (10, 100, 1_000, 4_000)}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
