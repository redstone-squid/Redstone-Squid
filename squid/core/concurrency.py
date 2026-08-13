"""Structured fan-out helpers built on anyio task groups.

asyncio.gather returns on the first exception and leaves its siblings running
unawaited, which for job batches means abandoned work still holding database
claims and temporary directories. A task group cancels the siblings and waits
for them, but returns nothing, so these helpers restore the one thing gather
was actually being used for: results in argument order.
"""

from collections.abc import Awaitable, Callable, Iterable
from typing import cast

import anyio


async def run_all[T](operations: Iterable[Callable[[], Awaitable[T]]]) -> list[T]:
    """Run every operation concurrently and return results in argument order.

    The first failure cancels the rest and propagates. anyio raises an
    ExceptionGroup when several fail at once, so callers that need to branch on
    a specific error should catch it with ``except*``.
    """
    factories = list(operations)
    if not factories:
        return []
    results: list[T] = cast(list[T], [None] * len(factories))

    async def run(index: int, factory: Callable[[], Awaitable[T]]) -> None:
        results[index] = await factory()

    async with anyio.create_task_group() as task_group:
        for index, factory in enumerate(factories):
            task_group.start_soon(run, index, factory)
    return results


async def run_all_awaitables[T](awaitables: Iterable[Awaitable[T]]) -> list[T]:
    """Run already-constructed awaitables concurrently, in argument order.

    Prefer :func:`run_all`. This exists for call sites that build their
    coroutines eagerly; every one of them is awaited exactly once, so none can
    be left un-awaited even if a sibling fails.
    """
    return await run_all([_as_factory(awaitable) for awaitable in awaitables])


def _as_factory[T](awaitable: Awaitable[T]) -> Callable[[], Awaitable[T]]:
    async def factory() -> T:
        return await awaitable

    return factory
