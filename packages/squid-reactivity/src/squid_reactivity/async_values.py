"""Shared execution and render observation for asynchronous reactive values."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from squid_reactivity.completion import Completion


class AsyncBinding[StatusT](Protocol):
    """An observed asynchronous value whose loader runs in the caller's task."""

    __slots__ = ()

    @property
    def pending(self) -> bool: ...

    async def resolve(self) -> StatusT:
        """Start or join work and return its status instead of unwrapping its value."""
        ...


class _Execution[StatusT]:
    """Run once in the first caller's task; other callers join its completion.

    The owner supplies the final status even when loading is interrupted. It may
    cancel completion explicitly when cancellation is a terminal outcome.
    Observers can wait before an owner calls run; waiting never starts work.
    """

    def __init__(self) -> None:
        self.completion: Completion[StatusT] = Completion()
        self._started = False

    @property
    def done(self) -> bool:
        """Whether execution and its cleanup have finished."""
        return self.completion.done

    async def wait(self) -> StatusT:
        """Observe completion without starting work or owning its cancellation."""
        return await self.completion.wait()

    async def run(self, loader: Callable[[], Awaitable[StatusT]], status: Callable[[], StatusT]) -> StatusT:
        if self._started:
            if not self.done:
                await self.wait()
            return status()
        self._started = True
        try:
            return await loader()
        finally:
            self.completion.resolve(status())


_ASYNC_BINDING_COLLECTOR: ContextVar[list[AsyncBinding[object]] | None] = ContextVar(
    "squid_reactivity_observed_async_bindings", default=None
)
"""Bindings accessed while expanding one render, separate from versioned dependencies.

The expansion scope owns the list. Nested scopes replace it and restore the outer list;
child tasks inherit that same list. Collection discovers work but never starts loaders.
"""


def _observe(binding: AsyncBinding[object]) -> None:
    observed = _ASYNC_BINDING_COLLECTOR.get()
    if observed is not None:
        observed.append(binding)


@contextmanager
def observe_async_bindings() -> Iterator[list[AsyncBinding[object]]]:
    """Collect asynchronous bindings accessed by one expanded component render."""
    observed: list[AsyncBinding[object]] = []
    token = _ASYNC_BINDING_COLLECTOR.set(observed)
    try:
        yield observed
    finally:
        _ASYNC_BINDING_COLLECTOR.reset(token)


def unique_async_bindings(bindings: list[AsyncBinding[object]]) -> tuple[AsyncBinding[object], ...]:
    """Preserve render order while removing repeat accesses to the same binding."""
    seen: set[int] = set()
    unique: list[AsyncBinding[object]] = []
    for binding in bindings:
        identity = id(binding)
        if identity not in seen:
            seen.add(identity)
            unique.append(binding)
    return tuple(unique)
