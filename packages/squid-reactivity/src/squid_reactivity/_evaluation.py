"""Detect cycles across computed values, resource loads, and operations."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

from squid_reactivity._errors import ReactiveCycleError


class _SettlingNode(Protocol):
    """A named derived value that can participate in cycle detection."""

    __slots__ = ()

    _label: str


_EVALUATION_STACK: ContextVar[tuple[_SettlingNode, ...]] = ContextVar("squid_reactivity_settling", default=())
"""The derived nodes currently producing a value on this task, outermost first.

Task-local rather than global: two independent values settled concurrently each copy the
context, so one cannot be mistaken for the other's dependency. A computed and a resource share
one stack because a chain can run through both, and a cycle should be named whole.
"""


def cycle_path(node: _SettlingNode) -> tuple[str, ...] | None:
    """Return the cycle ending at ``node``, or None if it is not already active.

    The path starts at the first occurrence of the node and repeats it at the
    end. Callers that led into the cycle are excluded from the diagnostic.
    """
    stack = _EVALUATION_STACK.get()
    for index, entered in enumerate(stack):
        if entered is node:
            return (*(held._label for held in stack[index:]), node._label)
    return None


@contextmanager
def settling(node: _SettlingNode) -> Iterator[None]:
    """Track a computation or load, raising before it re-enters an active dependency."""
    path = cycle_path(node)
    if path is not None:
        raise ReactiveCycleError(path)
    with _EVALUATION_STACK.set((*_EVALUATION_STACK.get(), node)):
        yield
