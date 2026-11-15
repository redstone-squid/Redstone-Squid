"""Measure computed cache costs with ``uv run scripts/benchmarks/reactivity_computed.py``.

Each sample creates an independent model and warms its committed cache before timing.
Evaluation counts expose whether reuse actually happened; timings are informational.
"""

import argparse
import platform
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median
from timeit import Timer

from squid_reactivity import Reactive, computed, state, transaction


class Model(Reactive):
    value: int = state(0)

    def __init__(self, work: int) -> None:
        self.work = work
        # Instrument evaluation without adding a reactive input to the benchmark.
        self.calls: list[None] = []

    @computed(reuse=True)
    def cheap(self) -> int:
        self.calls.append(None)
        return self.value * 2

    @computed(reuse=False)
    def cheap_local(self) -> int:
        self.calls.append(None)
        return self.value * 2

    @computed(reuse=True)
    def expensive(self) -> int:
        self.calls.append(None)
        return self.value + sum(index * index for index in range(self.work))

    @computed(reuse=False)
    def expensive_local(self) -> int:
        self.calls.append(None)
        return self.value + sum(index * index for index in range(self.work))

    @computed(reuse=True)
    def nested(self) -> int:
        self.calls.append(None)
        return self.expensive + 1


@dataclass(frozen=True)
class Scenario:
    name: str
    read: Callable[[Model], int]
    transactional: bool = False
    changed: bool = False

    def prepare(self, work: int) -> tuple[Model, Callable[[], int]]:
        model = Model(work)
        read = self.read
        read(model)

        if self.transactional:

            def operation() -> int:
                with transaction():
                    return read(model)

        elif self.changed:

            def operation() -> int:
                model.value += 1
                return read(model)

        else:

            def operation() -> int:
                return read(model)

        return model, operation


def measure(scenario: Scenario, *, iterations: int, samples: int, work: int) -> tuple[float, float]:
    durations: list[float] = []
    evaluations: list[float] = []
    for _ in range(samples):
        model, operation = scenario.prepare(work)
        before = len(model.calls)
        durations.append(Timer(operation).timeit(number=iterations) * 1_000_000 / iterations)
        evaluations.append((len(model.calls) - before) / iterations)
    return median(durations), median(evaluations)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        message = "must be a positive integer"
        raise argparse.ArgumentTypeError(message)
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=positive_int, default=1000, help="operations per sample (default: 1000)")
    parser.add_argument("--samples", type=positive_int, default=5, help="independent samples per case (default: 5)")
    parser.add_argument("--work", type=positive_int, default=5000, help="sum-of-squares workload size (default: 5000)")
    arguments = parser.parse_args()
    scenarios = (
        Scenario("warm cheap", lambda model: model.cheap),
        Scenario("warm cheap, reuse=False", lambda model: model.cheap_local),
        Scenario("changed cheap", lambda model: model.cheap, changed=True),
        Scenario("changed cheap, reuse=False", lambda model: model.cheap_local, changed=True),
        Scenario("transaction cheap", lambda model: model.cheap, transactional=True),
        Scenario("transaction cheap, reuse=False", lambda model: model.cheap_local, transactional=True),
        Scenario("transaction expensive", lambda model: model.expensive, transactional=True),
        Scenario("transaction expensive, reuse=False", lambda model: model.expensive_local, transactional=True),
        Scenario("transaction nested expensive", lambda model: model.nested, transactional=True),
    )
    print(f"{platform.python_implementation()} {platform.python_version()}")
    print(f"{arguments.samples} samples, {arguments.iterations} iterations/sample, workload {arguments.work}")
    print(f"{'case':<38} {'median us/op':>14} {'body evaluations/op':>21}")
    for scenario in scenarios:
        duration, evaluations = measure(
            scenario, iterations=arguments.iterations, samples=arguments.samples, work=arguments.work
        )
        print(f"{scenario.name:<38} {duration:>14.3f} {evaluations:>21.3f}")


if __name__ == "__main__":
    main()
