"""on_change reports a changed result once per commit, from committed state."""

import gc
import logging

import pytest

from squid_reactivity import Reactive, Signal, computed, on_change, state, transaction


class Cart(Reactive):
    apples: int = state(0)
    pears: int = state(0)
    discounted: bool = state(False)
    discount: int = state(0)

    @computed
    def total(self) -> int:
        return self.apples + self.pears

    @computed
    def parity(self) -> int:
        return self.total % 2


def test_a_transaction_writing_several_fields_reports_once() -> None:
    cart = Cart()
    seen: list[int] = []
    listener = on_change(lambda: cart.total, seen.append)

    assert seen == []  # The initial value is not reported.
    with transaction():
        cart.apples = 2
        cart.pears = 3
        assert seen == []
    cart.apples = 4

    assert seen == [5, 7]
    listener.close()


def test_a_write_that_leaves_the_result_equal_is_not_reported() -> None:
    cart = Cart()
    seen: list[int] = []
    with on_change(lambda: cart.parity, seen.append):
        cart.apples = 2
        cart.apples = 3
        cart.apples = 5

    assert seen == [1]


def test_dependencies_follow_the_branch_the_read_took() -> None:
    cart = Cart()
    seen: list[int] = []
    with on_change(lambda: cart.total - cart.discount if cart.discounted else cart.total, seen.append):
        cart.discount = 1  # Not read yet, so nothing recomputes.
        cart.discounted = True
        cart.discount = 2

    assert seen == [-1, -2]


def test_a_rolled_back_transaction_reports_nothing() -> None:
    cart = Cart()
    seen: list[int] = []
    with on_change(lambda: cart.total, seen.append), pytest.raises(RuntimeError, match="stop"), transaction():
        cart.apples = 9
        raise RuntimeError("stop")

    assert seen == []


def test_a_notification_inside_an_open_transaction_reads_committed_state() -> None:
    """A signal notifies immediately, even while the transaction's writes are only staged."""
    cart = Cart()
    signal = Signal()
    seen: list[int] = []

    def read() -> int:
        signal.track()
        return cart.total

    with on_change(read, seen.append):
        with transaction():
            cart.apples = 2
            signal.notify()
            assert seen == []
        assert seen == [2]


def test_the_callback_writes_through_its_own_transaction(caplog: pytest.LogCaptureFixture) -> None:
    cart = Cart()

    def write_directly(total: int) -> None:
        cart.discount = total

    with caplog.at_level(logging.ERROR), on_change(lambda: cart.apples, write_directly):
        cart.apples = 1
    assert cart.discount == 0
    assert "cannot assign reactive state directly" in caplog.text

    def write_in_transaction(total: int) -> None:
        with transaction():
            cart.discount = total

    with on_change(lambda: cart.apples, write_in_transaction):
        cart.apples = 2
    assert cart.discount == 2


def test_a_failing_read_is_retried_on_the_next_change(caplog: pytest.LogCaptureFixture) -> None:
    cart = Cart()
    seen: list[int] = []

    def read() -> int:
        if cart.apples == 1:
            raise ValueError("one apple")
        return cart.apples

    with caplog.at_level(logging.ERROR), on_change(read, seen.append):
        cart.apples = 1
        cart.apples = 2

    assert seen == [2]
    assert "one apple" in caplog.text


def test_closing_or_dropping_the_listener_stops_the_calls() -> None:
    cart = Cart()
    seen: list[int] = []

    listener = on_change(lambda: cart.apples, seen.append)
    listener.close()
    cart.apples = 1

    on_change(lambda: cart.apples, seen.append)
    gc.collect()
    cart.apples = 2

    assert seen == []
