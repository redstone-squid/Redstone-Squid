"""Utility functions."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Callable, Coroutine, Iterable
from typing import Any


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
def signature_from[**P, T](_original: Callable[P, T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return _decorator


_background_tasks: set[asyncio.Task[Any]] = set()


def fire_and_forget(
    coro: Coroutine[None, None, Any], *, bg_set: set[asyncio.Task[Any] | Any] = _background_tasks
) -> None:
    """Runs a coroutine in the background without waiting for it to finish."""
    task = asyncio.create_task(coro)
    bg_set.add(task)
    task.add_done_callback(bg_set.discard)


async def _aiterator[T](it: Iterable[T]) -> AsyncIterator[T]:
    for item in it:
        yield item


def async_iterator[T](it: Iterable[T] | AsyncIterable[T]) -> AsyncIterator[T]:
    """Wraps an Iterable or AsyncIterable into an AsyncIterator."""
    try:
        iterator = iter(it)  # pyright: ignore
        return _aiterator(iterator)
    except TypeError:
        # If it is an AsyncIterable, we can directly use it
        if isinstance(it, AsyncIterable):
            return it.__aiter__()
        else:
            raise TypeError(f"Expected Iterable or AsyncIterable, got {type(it)}")
