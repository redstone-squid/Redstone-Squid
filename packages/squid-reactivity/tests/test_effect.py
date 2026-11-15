"""An effect reruns after what it read changes, and may assign state itself."""

import gc
import logging
from collections.abc import Callable

import pytest

from squid_reactivity import Reactive, computed, effect, on_change, state, transaction


class Cart(Reactive):
    apples: int = state(0)
    pears: int = state(0)
    total: int = state(0)
    detailed: bool = state(False)

    @computed
    def parity(self) -> int:
        return self.apples % 2


def test_an_effect_runs_at_creation_and_after_each_change_to_what_it_read() -> None:
    cart = Cart()
    seen: list[int] = []
    with effect(lambda: seen.append(cart.apples)):
        assert seen == [0]
        cart.apples = 1
        cart.pears = 5  # Not read, so no run.
        with transaction():
            cart.apples = 2
            cart.apples = 3

    assert seen == [0, 1, 3]


def test_a_write_that_changes_nothing_the_effect_read_does_not_rerun_it() -> None:
    cart = Cart()
    seen: list[int] = []
    with effect(lambda: seen.append(cart.parity)):
        cart.apples = 2
        cart.apples = 3

    assert seen == [0, 1]


def test_an_effect_follows_the_branch_it_took() -> None:
    cart = Cart()
    seen: list[int] = []
    with effect(lambda: seen.append(cart.pears if cart.detailed else -1)):
        cart.pears = 1
        cart.detailed = True
        cart.pears = 2

    assert seen == [-1, 1, 2]


def test_an_effect_may_assign_state() -> None:
    cart = Cart()

    def update_total() -> None:
        cart.total = cart.apples + cart.pears

    with effect(update_total):
        with transaction():
            cart.apples = 2
            cart.pears = 3
        assert cart.total == 5


def test_an_effect_that_assigns_what_it_reads_reruns_until_nothing_changes() -> None:
    cart = Cart()
    runs: list[int] = []

    def fill() -> None:
        runs.append(cart.apples)
        if cart.apples < 3:
            cart.apples += 1

    with effect(fill):
        assert cart.apples == 3
    assert runs == [0, 1, 2, 3]


def test_a_transaction_inside_an_effect_publishes_its_writes_together() -> None:
    cart = Cart()
    sums: list[int] = []

    def mirror() -> None:
        apples = cart.apples
        with transaction():
            cart.pears = apples
            cart.total = apples * 2

    with effect(mirror), on_change(lambda: cart.pears + cart.total, sums.append):
        cart.apples = 2

    assert sums == [6]


def test_an_effect_created_in_a_transaction_first_runs_when_it_ends() -> None:
    cart = Cart()
    seen: list[int] = []
    with transaction():
        cart.apples = 4
        running = effect(lambda: seen.append(cart.apples))
        assert seen == []
    assert seen == [4]
    running.close()


def test_the_cleanup_runs_before_the_next_run_and_on_close() -> None:
    cart = Cart()
    events: list[str] = []

    def run() -> Callable[[], None]:
        apples = cart.apples
        events.append(f"run {apples}")
        return lambda: events.append(f"clean {apples}")

    running = effect(run)
    cart.apples = 1
    running.close()
    running.close()

    assert events == ["run 0", "clean 0", "run 1", "clean 1"]


def test_a_failing_effect_reruns_after_the_next_change(caplog: pytest.LogCaptureFixture) -> None:
    cart = Cart()
    seen: list[int] = []

    def run() -> None:
        if cart.apples == 1:
            raise ValueError("one apple")
        seen.append(cart.apples)

    with caplog.at_level(logging.ERROR), effect(run):
        cart.apples = 1
        cart.apples = 2

    assert seen == [0, 2]
    assert "one apple" in caplog.text


def test_returning_a_value_is_reported_as_a_mistake(caplog: pytest.LogCaptureFixture) -> None:
    cart = Cart()
    with caplog.at_level(logging.ERROR), effect(lambda: cart.apples):  # type: ignore[arg-type]
        pass

    assert "returns a cleanup function or None, not int" in caplog.text


def test_an_effect_that_never_settles_is_stopped(caplog: pytest.LogCaptureFixture) -> None:
    cart = Cart()

    def grow() -> None:
        cart.apples += 1

    with caplog.at_level(logging.ERROR), effect(grow):
        assert cart.apples == 100
        assert "keeps changing what it reads" in caplog.text


def test_closing_or_dropping_the_effect_stops_it() -> None:
    cart = Cart()
    seen: list[int] = []

    effect(lambda: seen.append(cart.apples)).close()
    cart.apples = 1

    effect(lambda: seen.append(cart.apples))
    gc.collect()
    cart.apples = 2

    assert seen == [0, 1]
