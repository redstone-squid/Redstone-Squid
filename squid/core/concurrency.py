"""Structured fan-out helpers built on anyio task groups.

A task group cancels and awaits its siblings, unlike `asyncio.gather`, but returns nothing; these
helpers add back results in argument order.

One helper per failure policy: :func:`run_all` cancels the siblings on the first failure,
:func:`run_all_settled` lets every operation finish and then raises, and :func:`settle_all` hands
the failures back as values. All three run on :func:`task_group`, which is also the one to reach
for directly when a block supervises a task of its own rather than a fan-out.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager, nullcontext
from typing import cast

import anyio
import anyio.abc

DISCORD_FANOUT_LIMIT = 5
"""Concurrent in-flight calls per Discord fan-out.

discord.py already serializes and retries per rate-limit bucket, so this is not
a rate limiter -- it bounds how many message fetches or edits a single build can
have outstanding at once, which is otherwise unbounded in the message count.
"""


def _limiter(limit: int | None) -> anyio.CapacityLimiter | nullcontext[None]:
    return anyio.CapacityLimiter(limit) if limit is not None else nullcontext()


@asynccontextmanager
async def task_group() -> AsyncIterator[anyio.abc.TaskGroup]:
    """The anyio task group, without the ExceptionGroup around a lone failure.

    The scope ends when the block exits, which waits for the children and cancels them if the body
    or a sibling fails -- anyio's semantics exactly. Only the exception differs: a lone failure
    raises as itself, because a group of one matches neither ``except SquidError`` nor the
    isinstance checks call sites classify by. Several failures still raise as a group, and a
    child's own group passes through.
    """
    failure: BaseException | None = None
    try:
        async with anyio.create_task_group() as tasks:
            yield tasks
    except BaseExceptionGroup as raised:
        if len(raised.exceptions) > 1:
            raise
        failure = raised.exceptions[0]
    # Re-raised outside the except clause, so the group anyio wrapped it in does
    # not become its __context__ and bury the cause it was raised from.
    if failure is not None:
        raise failure


def _failure(failures: Sequence[BaseException]) -> BaseException:
    """Pick what a fan-out raises: the lone failure itself, or a group of them.

    A single failure goes unwrapped so that call sites classifying by type still match it.
    """
    if len(failures) == 1:
        return failures[0]
    return BaseExceptionGroup("A fan-out operation failed.", failures)


async def run_all[T](operations: Iterable[Callable[[], Awaitable[T]]], *, limit: int | None = None) -> list[T]:
    """Run every operation concurrently and return results in argument order.

    The first failure cancels the rest and propagates, so use this only where a cancelled sibling
    strands nothing; where an operation owns state its own error handling must release, use
    :func:`run_all_settled`. One failure propagates as itself, several as an ExceptionGroup.

    Args:
        operations: Zero-argument callables, each returning an awaitable.
        limit: Maximum operations in flight at once. ``None`` runs them all.
    """
    factories = list(operations)
    if not factories:
        return []
    results: list[T] = cast(list[T], [None] * len(factories))
    limiter = _limiter(limit)

    async def run(index: int, factory: Callable[[], Awaitable[T]]) -> None:
        async with limiter:
            results[index] = await factory()

    async with task_group() as tasks:
        for index, factory in enumerate(factories):
            tasks.start_soon(run, index, factory)
    return results


async def run_all_settled[T](operations: Iterable[Callable[[], Awaitable[T]]], *, limit: int | None = None) -> list[T]:
    """Run every operation to completion, then raise if any of them failed.

    Unlike :func:`run_all`, a failure cancels nothing, so every operation reaches its own
    ``except`` clause -- which for a claimed job is what marks it failed and releases the claim
    rather than stranding it until the lease expires.

    Args:
        operations: Zero-argument callables, each returning an awaitable.
        limit: Maximum operations in flight at once. ``None`` runs them all.
    """
    outcomes = await settle_all(operations, limit=limit)
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    if failures:
        raise _failure(failures)
    return cast(list[T], outcomes)


async def settle_all[T](
    operations: Iterable[Callable[[], Awaitable[T]]], *, limit: int | None = None
) -> list[T | Exception]:
    """Run every operation to completion, returning each result or its failure.

    The bounded, structured form of ``gather(..., return_exceptions=True)``, for best-effort
    fan-outs where one branch failing must not stop the others. Only ``Exception`` is captured,
    never ``BaseException``, so cancellation still tears the whole fan-out down.
    """
    factories = list(operations)
    if not factories:
        return []
    results: list[T | Exception] = cast(list[T | Exception], [None] * len(factories))
    limiter = _limiter(limit)

    async def run(index: int, factory: Callable[[], Awaitable[T]]) -> None:
        async with limiter:
            try:
                results[index] = await factory()
            except Exception as error:
                results[index] = error

    async with task_group() as tasks:
        for index, factory in enumerate(factories):
            tasks.start_soon(run, index, factory)
    return results


async def run_all_awaitables[T](awaitables: Iterable[Awaitable[T]]) -> list[T]:
    """Run already-constructed awaitables concurrently, in argument order.

    Prefer :func:`run_all`. This is for call sites that build their coroutines eagerly; each is
    awaited exactly once, so none is left un-awaited even if a sibling fails.
    """
    return await run_all([_as_factory(awaitable) for awaitable in awaitables])


def _as_factory[T](awaitable: Awaitable[T]) -> Callable[[], Awaitable[T]]:
    async def factory() -> T:
        return await awaitable

    return factory
