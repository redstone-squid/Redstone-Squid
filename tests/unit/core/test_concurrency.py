"""Structured fan-out helper tests."""

import anyio
import pytest

from squid.core.concurrency import run_all, run_all_awaitables


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

    with anyio.fail_after(5), pytest.raises(BaseExceptionGroup) as caught:
        await run_all([lambda: failing(), lambda: sibling()])

    assert sibling_cancelled is True
    assert [type(error) for error in caught.value.exceptions] == [RuntimeError]


async def test_run_all_awaitables_awaits_every_coroutine() -> None:
    """Eagerly-built coroutines must all be awaited, or Python warns about the rest."""

    async def value(number: int) -> int:
        return number

    assert await run_all_awaitables([value(1), value(2), value(3)]) == [1, 2, 3]
