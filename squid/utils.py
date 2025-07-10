"""Utility functions."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


# https://stackoverflow.com/questions/74714300/paramspec-for-a-pre-defined-function-without-using-generic-callablep
def signature_from[**P, T](_original: Callable[P, T]) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Copies the signature of a function to another function."""

    def _decorator(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return _decorator


_background_tasks: set[asyncio.Task] = set()
def fire_and_forget(coro: Coroutine[None, None, Any]) -> None:
    """Runs a coroutine in the background without waiting for it to finish."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
