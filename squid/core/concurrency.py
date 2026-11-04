"""Run related async operations with a shared lifetime and ordered results.

These helpers wait for child tasks to finish or clean up before returning, so
a failed batch cannot leave tasks using its database claims or temporary files.
Choose a helper by its failure policy. `run_all` cancels siblings when one fails.
`run_all_settled` waits for ordinary failures before raising, and `settle_all`
returns those failures as values. All return results in input order. Use
`task_group` directly when children do not need to return a list of results.
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
    """Run child tasks and unwrap a single failure from AnyIO's exception group.

    Leaving the block waits for its children. A failure in the body or a child
    cancels the other children. If only one failure escapes, raise it directly so
    callers can catch its original type, for example with `except SquidError`.
    Multiple failures remain grouped. A group raised by a child is preserved;
    only the outer wrapper is removed.
    """
    failure: BaseException | None = None
    try:
        async with anyio.create_task_group() as tasks:
            yield tasks
    except BaseExceptionGroup as raised:
        # `!= 1` rather than `> 1`: an exception group cannot be empty, and spelling it this way
        # says so to the checker instead of indexing a tuple it has to assume may be empty.
        if len(raised.exceptions) != 1:
            raise
        failure = raised.exceptions[0]
    # Re-raised outside the except clause, so the group anyio wrapped it in does
    # not become its __context__ and bury the cause it was raised from.
    if failure is not None:
        raise failure


def _failure(failures: Sequence[BaseException]) -> BaseException:
    """Return a lone failure directly, or combine multiple failures into a group."""
    if len(failures) == 1:
        return failures[0]
    return BaseExceptionGroup("A fan-out operation failed.", failures)


async def run_all[T](operations: Iterable[Callable[[], Awaitable[T]]], *, limit: int | None = None) -> list[T]:
    """Run operations concurrently and return their results in input order.

    A failure cancels siblings and waits for their cleanup. Use `run_all_settled`
    when ordinary failures must not interrupt siblings, for example when each
    job must record its own outcome before the batch returns.

    Args:
        operations: Zero-argument callables, each returning an awaitable.
        limit: Maximum operations in flight at once. None runs them all.
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
    """Wait for all operations, then raise any ordinary failures.

    Successful results keep input order. One failure is raised directly; several
    are grouped. Cancellation and other BaseException subclasses still interrupt
    the batch. An Exception returned as a result is also treated as a failure.

    Args:
        operations: Zero-argument callables, each returning an awaitable.
        limit: Maximum operations in flight at once. None runs them all.
    """
    outcomes = await settle_all(operations, limit=limit)
    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    if failures:
        raise _failure(failures)
    return cast(list[T], outcomes)


async def settle_all[T](
    operations: Iterable[Callable[[], Awaitable[T]]], *, limit: int | None = None
) -> list[T | Exception]:
    """Return each operation's result or exception in input order.

    An ordinary failure does not cancel siblings. For example, a permission error
    in one Discord channel need not prevent updates to other channels. Cancellation
    and other BaseException subclasses still propagate and stop the batch.
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
    """Run existing awaitables and return their results in input order.

    Failures follow `run_all`'s cancellation policy. Prefer `run_all` when callers
    can supply factories instead of constructing awaitables in advance.
    """
    return await run_all([_as_factory(awaitable) for awaitable in awaitables])


def _as_factory[T](awaitable: Awaitable[T]) -> Callable[[], Awaitable[T]]:
    async def factory() -> T:
        return await awaitable

    return factory
