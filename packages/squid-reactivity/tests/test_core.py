"""Standalone contracts for the transactional state kernel."""

import asyncio
from typing import override

import pytest

from squid_reactivity import (
    Reactive,
    ReactiveConflictError,
    ReactiveWriteError,
    StaleReactiveContextError,
    computed,
    observe_reads,
    state,
    strong_read,
    transaction,
)


class Counter(Reactive):
    value: int = state(0)

    def __init__(self) -> None:
        self.commits: list[frozenset[str]] = []

    @computed
    def doubled(self) -> int:
        return self.value * 2

    @override
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
        # Deliberately outlive the transaction to exercise an inherited stale context.
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
        # Deliberately outlive the transaction to exercise an inherited stale context.
        escaped = asyncio.create_task(later())
        counter.value = 4

    released.set()
    assert await escaped == 4


def test_a_synchronous_transaction_is_confined_to_nobody() -> None:
    counter = Counter()
    with transaction():
        counter.value = 3
    assert counter.value == 3


class Preferences(Reactive):
    theme: str = state("light")
    locale: str = state("en")


class Label(Reactive):
    preferences: Preferences = state(opaque=True)

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    @computed
    def text(self) -> str:
        return f"theme={self.preferences.theme}"


async def test_a_blind_write_moves_the_version_past_the_action_that_beat_it() -> None:
    """A later commit must advance the version even if both actions staged the same version.

    The computed value is read between commits. If the second commit reused
    the first version, that cached value would appear current and never update.
    """
    preferences = Preferences()
    label = Label(preferences)
    staged = asyncio.Event()
    published = asyncio.Event()

    async def slow() -> None:
        with transaction():
            preferences.theme = "slow"
            staged.set()
            await published.wait()

    async def quick() -> None:
        await staged.wait()
        with transaction():
            preferences.theme = "quick"
        assert label.text == "theme=quick", "the reader settles against the first commit"
        published.set()

    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(slow())
        tasks.create_task(quick())

    assert preferences.theme == "slow", "last commit wins"
    assert label.text == "theme=slow", "and the reader is told the world moved"


async def test_a_strong_read_is_not_satisfied_by_a_version_a_later_write_reused() -> None:
    """A strong read must detect a later commit that replaces the value it read.

    The slow action stages first. The quick action commits, and the guarded
    action reads its result. The slow action then commits over that result.
    If it reused the quick action's version, the guarded action would miss the
    conflict and publish a result based on an obsolete value.
    """
    preferences = Preferences()
    staged = asyncio.Event()
    quick_committed = asyncio.Event()
    guarded_read = asyncio.Event()
    slow_committed = asyncio.Event()

    async def slow() -> None:
        with transaction():
            preferences.theme = "slow"
            staged.set()
            await guarded_read.wait()
        slow_committed.set()

    async def quick() -> None:
        await staged.wait()
        with transaction():
            preferences.theme = "quick"
        quick_committed.set()

    async def guarded() -> None:
        await quick_committed.wait()
        with pytest.raises(ReactiveConflictError), transaction():
            with strong_read():
                assert preferences.theme == "quick"
            preferences.locale = "fr"
            guarded_read.set()
            await slow_committed.wait()

    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(slow())
        tasks.create_task(quick())
        tasks.create_task(guarded())

    assert preferences.locale == "en", "the action that branched on a stale read published nothing"
