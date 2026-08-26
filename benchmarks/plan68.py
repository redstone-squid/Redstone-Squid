"""Small reproducible latency and retention baseline for Plan 68.

Run the core comparison with a selected ``PYTHONPATH`` and ``--core-only``. Run without that
flag in the current workspace to include bounded ledger and history retention. This is deliberately
not a pytest benchmark and never discovers or runs the repository test suite.
"""

import argparse
import gc
import json
import statistics
import time
import tracemalloc
from collections.abc import Callable
from typing import Any

from squid_reactive import LocalTopicBus, StateOwner, SharedState, state, transaction


def _shared_type(cells: int) -> type[SharedState[str]]:
    namespace: dict[str, Any] = {"__annotations__": {f"value_{index}": int for index in range(cells)}}
    namespace.update({f"value_{index}": state(0) for index in range(cells)})
    return type(f"BenchmarkShared{cells}", (SharedState,), namespace)


def _latency(operation: Callable[[], None], iterations: int) -> int:
    samples: list[int] = []
    operation()
    gc.collect()
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(7):
            started = time.perf_counter_ns()
            for _ in range(iterations):
                operation()
            samples.append((time.perf_counter_ns() - started) // iterations)
    finally:
        if enabled:
            gc.enable()
    return int(statistics.median(samples))


def _peak(operation: Callable[[], None], iterations: int) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        for _ in range(iterations):
            operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def core_baseline() -> dict[str, dict[str, int]]:
    """Measure publishing transactions over 0/1/10/100 strongly read SharedState cells."""
    results: dict[str, dict[str, int]] = {}
    for cells in (0, 1, 10, 100):
        model = _shared_type(cells)(LocalTopicBus(), "benchmark")

        def publish(model: SharedState[str] = model, cells: int = cells) -> None:
            with transaction():
                for index in range(cells):
                    name = f"value_{index}"
                    setattr(model, name, getattr(model, name) + 1)

        iterations = max(25, 2_000 // max(1, cells))
        results[str(cells)] = {
            "median_ns": _latency(publish, iterations),
            "peak_bytes": _peak(publish, min(iterations, 200)),
            "iterations": iterations,
        }
    return results


def retention_baseline() -> dict[str, int]:
    """Measure retained bytes for bounded action outcomes and conditional history entries."""
    from squid_ui.runtime import History
    from squid_reactive import ActionLedger, add_action_outcome_sink

    ledger = ActionLedger(limit=100)
    add_action_outcome_sink(ledger)
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(100):
        with transaction():
            pass
    after = tracemalloc.take_snapshot()
    ledger_bytes = sum(max(0, item.size_diff) for item in after.compare_to(before, "filename"))
    ledger.close()
    tracemalloc.stop()

    class Owner(StateOwner):
        value: int = state(0)

        def invalidate(self) -> None:
            pass

    owner = Owner()
    history = History(owner, limit=100)
    gc.collect()
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for value in range(1, 101):
        with transaction():
            history.record(f"value {value}")
            owner.value = value
    after = tracemalloc.take_snapshot()
    history_bytes = sum(max(0, item.size_diff) for item in after.compare_to(before, "filename"))
    tracemalloc.stop()
    return {"ledger_100_bytes": ledger_bytes, "history_100_bytes": history_bytes}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-only", action="store_true")
    args = parser.parse_args()
    result: dict[str, object] = {"core": core_baseline()}
    if not args.core_only:
        result["retention"] = retention_baseline()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
