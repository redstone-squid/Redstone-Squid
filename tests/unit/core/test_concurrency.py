"""Structured fan-out helper tests."""

import anyio
import pytest

from squid.core.concurrency import run_all, run_all_awaitables, run_all_settled, settle_all


async def test_run_all_returns_results_in_argument_order() -> None:
    async def make(value: int, delay: float) -> int:
        await anyio.sleep(delay)
        return value

    # Longest first, so completion order is the reverse of argument order.
    results = await run_all([lambda: make(1, 0.03), lambda: make(2, 0.02), lambda: make(3, 0.0)])

    assert results == [1, 2, 3]


async def test_run_all_handles_an_empty_batch() -> None:
    assert await run_all([]) == []


async def test_run_all_cancels_siblings_on_the_first_failure() -> None:
    """The whole point over gather: no sibling is left running unawaited."""
    sibling_cancelled = False
    started = anyio.Event()

    async def failing() -> None:
        await started.wait()
        msg = "boom"
        raise RuntimeError(msg)

    async def sibling() -> None:
        nonlocal sibling_cancelled
        started.set()
        try:
            await anyio.sleep(30)
        except anyio.get_cancelled_exc_class():
            sibling_cancelled = True
            raise

    # Unwrapped, not an ExceptionGroup: call sites classify by type, and an
    # ``except RuntimeError`` upstream has to keep matching.
    with anyio.fail_after(5), pytest.raises(RuntimeError):
        await run_all([lambda: failing(), lambda: sibling()])

    assert sibling_cancelled is True


async def test_run_all_groups_simultaneous_failures() -> None:
    async def failing(error: Exception) -> None:
        raise error

    with pytest.raises(BaseExceptionGroup) as caught:
        await run_all([lambda: failing(RuntimeError("first")), lambda: failing(ValueError("second"))])

    assert [type(error) for error in caught.value.exceptions] == [RuntimeError, ValueError]


async def test_run_all_preserves_a_lone_failures_cause() -> None:
    """Unwrapping must not rewrite the chain the operation raised its error with."""

    async def failing() -> None:
        cause = ValueError("root")
        msg = "wrapped"
        raise RuntimeError(msg) from cause

    with pytest.raises(RuntimeError) as caught:
        await run_all([failing])

    assert isinstance(caught.value.__cause__, ValueError)
    assert not isinstance(caught.value.__context__, BaseExceptionGroup)


async def test_run_all_respects_the_concurrency_limit() -> None:
    in_flight = 0
    peak = 0

    async def work() -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await anyio.sleep(0.01)
        in_flight -= 1

    await run_all([work] * 12, limit=3)

    assert peak == 3


async def test_settle_all_runs_every_branch_despite_a_failure() -> None:
    """Best-effort fan-out: one denied channel must not skip the others."""
    completed: list[int] = []

    def make(number: int):
        async def work() -> int:
            if number == 1:
                msg = "denied"
                raise PermissionError(msg)
            await anyio.sleep(0.01)
            completed.append(number)
            return number

        return work

    outcomes = await settle_all([make(n) for n in range(4)], limit=2)

    assert completed == [0, 2, 3]
    assert isinstance(outcomes[1], PermissionError)
    assert [outcomes[0], outcomes[2], outcomes[3]] == [0, 2, 3]


async def test_settle_all_still_propagates_cancellation() -> None:
    """Capturing Exception must not swallow the caller's cancellation."""
    started = anyio.Event()

    async def work() -> None:
        started.set()
        await anyio.sleep(30)

    with anyio.move_on_after(0.5) as scope:
        async with anyio.create_task_group() as tg:
            tg.start_soon(lambda: settle_all([work]))
            await started.wait()
            tg.cancel_scope.cancel()

    assert scope.cancelled_caught is False


async def test_run_all_awaitables_awaits_every_coroutine() -> None:
    """Eagerly-built coroutines must all be awaited, or Python warns about the rest."""

    async def value(number: int) -> int:
        return number

    assert await run_all_awaitables([value(1), value(2), value(3)]) == [1, 2, 3]


async def test_run_all_settled_finishes_every_operation_before_raising() -> None:
    """A cancelled job never reaches its own except clause, so nothing may be cancelled."""
    finished: list[int] = []
    started = anyio.Event()

    async def failing() -> None:
        started.set()
        msg = "boom"
        raise RuntimeError(msg)

    async def sibling(number: int) -> None:
        await started.wait()
        await anyio.sleep(0.01)
        finished.append(number)

    with anyio.fail_after(5), pytest.raises(RuntimeError):
        await run_all_settled([lambda: failing(), lambda: sibling(1), lambda: sibling(2)])

    assert finished == [1, 2]


async def test_run_all_settled_returns_results_in_argument_order() -> None:
    async def make(value: int, delay: float) -> int:
        await anyio.sleep(delay)
        return value

    assert await run_all_settled([lambda: make(1, 0.02), lambda: make(2, 0.0)]) == [1, 2]


async def test_run_all_settled_groups_several_failures() -> None:
    async def failing(error: Exception) -> None:
        raise error

    with pytest.raises(BaseExceptionGroup) as caught:
        await run_all_settled([lambda: failing(RuntimeError("first")), lambda: failing(ValueError("second"))])

    assert [type(error) for error in caught.value.exceptions] == [RuntimeError, ValueError]
