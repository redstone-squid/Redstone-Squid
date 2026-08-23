"""Standalone contracts for the transactional state kernel."""

import pytest

from squid_reactive import Reactive, ReactiveWriteError, computed, observe_reads, state, transaction


class Counter(Reactive):
    value: int = state(0)

    def __init__(self) -> None:
        self.commits: list[frozenset[str]] = []

    @computed
    def doubled(self) -> int:
        return self.value * 2

    def on_state_commit(self, names: frozenset[str]) -> None:
        self.commits.append(names)


def test_transaction_publishes_once_and_computed_reads_staged_state() -> None:
    counter = Counter()

    with transaction():
        counter.value = 2
        assert counter.value == 2
        assert counter.doubled == 4

    assert counter.value == 2
    assert counter.commits == [frozenset({"__state_value"})]


def test_transaction_rollback_discards_staged_state() -> None:
    counter = Counter()

    with pytest.raises(RuntimeError, match="stop"), transaction():
        counter.value = 2
        raise RuntimeError("stop")

    assert counter.value == 0
    assert counter.commits == []


def test_observed_state_cannot_be_written() -> None:
    counter = Counter()

    with pytest.raises(ReactiveWriteError, match="while a render was reading"), observe_reads():
        counter.value = 1


def test_construction_inside_an_observation_may_assign_declared_state() -> None:
    class Required(Reactive):
        value: int = state()

        def __init__(self, value: int) -> None:
            self.value = value

    with observe_reads():
        child = Required(3)

    assert child.value == 3
