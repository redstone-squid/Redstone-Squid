"""Standalone contracts for the transactional state kernel."""

import asyncio

import pytest

from squid_reactivity import (
    LocalTopicBus,
    ReactiveConflictError,
    ReactiveWriteError,
    SharedState,
    StaleReactiveContextError,
    StateOwner,
    computed,
    observe_reads,
    state,
    strong_read,
    transaction,
)


class Counter(StateOwner):
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
    class Required(StateOwner):
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


class Preferences(SharedState[None]):
    theme: str = state("light")
    locale: str = state("en")


class Label(StateOwner):
    preferences: Preferences = state(opaque=True)

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    @computed
    def text(self) -> str:
        return f"theme={self.preferences.theme}"


async def test_a_blind_write_moves_the_version_past_the_action_that_beat_it() -> None:
    """Last commit wins the value; it must not also inherit the loser's version.

    Both actions stage `version + 1` from the same starting version, so publishing that
    number verbatim leaves the second commit standing on the first one's version -- and
    every reader settled against it holding a value the cell no longer has.
    """
    bus = LocalTopicBus()
    preferences = Preferences(bus)
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
    """`strong_read` compares versions, so a reused version would silently satisfy it.

    Three actions, because two cannot tell the bug from correct behaviour: `slow` stages
    blindly at the starting version, `quick` commits on top of it, and `guarded` branches on
    what `quick` published. `slow` then replaces that value -- and unless its commit moves the
    version past `quick`'s, `guarded` serializes against a value that is no longer there.
    """
    bus = LocalTopicBus()
    preferences = Preferences(bus)
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
