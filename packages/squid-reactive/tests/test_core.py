"""Standalone contracts for the transactional state kernel."""

import asyncio

import pytest

from squid_reactive import (
    Reactive,
    ReactiveWriteError,
    StaleReactiveContextError,
    computed,
    observe_reads,
    state,
    transaction,
)


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


async def test_a_task_outliving_the_action_cannot_stage_into_it() -> None:
    counter = Counter()
    released = asyncio.Event()

    async def later() -> None:
        await released.wait()
        counter.value = 99

    with transaction():
        escaped = asyncio.create_task(later())
        counter.value = 1

    released.set()
    with pytest.raises(StaleReactiveContextError, match="already finished"):
        await escaped
    assert counter.value == 1


async def test_sibling_tasks_cannot_stage_into_one_transaction() -> None:
    counter = Counter()

    async def branch(value: int) -> None:
        counter.value = value

    with pytest.raises(StaleReactiveContextError, match="other than the one that opened"), transaction():
        await asyncio.gather(branch(1), branch(2))


async def test_sibling_tasks_may_read_the_action_they_run_under() -> None:
    counter = Counter()
    seen: list[int] = []

    async def branch() -> None:
        seen.append(counter.value)

    with transaction():
        counter.value = 7
        await asyncio.gather(branch(), branch())

    assert seen == [7, 7]


async def test_a_finished_transaction_reads_as_though_absent() -> None:
    counter = Counter()
    released = asyncio.Event()

    async def later() -> int:
        await released.wait()
        return counter.value

    with transaction():
        escaped = asyncio.create_task(later())
        counter.value = 4

    released.set()
    assert await escaped == 4


def test_a_synchronous_transaction_is_confined_to_nobody() -> None:
    counter = Counter()
    with transaction():
        counter.value = 3
    assert counter.value == 3
