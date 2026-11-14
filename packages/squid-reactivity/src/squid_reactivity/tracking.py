"""Capture dependencies and check whether a computation's inputs are still current.

Computed values, resources, and renders share a dependency collector. Cycle detection
and invalidation epochs support evaluation; cells and transaction rules live separately.
"""

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from squid_reactivity.addresses import CellAddress
from squid_reactivity.errors import ReactiveCycleError
from squid_reactivity.transaction_state import read_only

if TYPE_CHECKING:
    from squid_reactivity.topics import Topic


class _ReactiveSource(Protocol):
    """A versioned value with explicit upstream dependencies and a notification address.

    Settling may change the upstream graph without changing the value's version.
    Address traversal therefore follows the live graph after validation.
    """

    @property
    def address(self) -> Topic | CellAddress | None: ...

    def dependencies(self) -> Iterable[_ReactiveSource]: ...

    def settle(self) -> int: ...


class _DependencyCollector(Protocol):
    """Collect dependencies read by a computed value, resource load, or render."""

    sources: dict[_ReactiveSource, int]


_DEPENDENCY_COLLECTOR: ContextVar[_DependencyCollector | None] = ContextVar("squid_reactivity_consumer", default=None)


def _equal(left: object, right: object) -> bool:
    """Compare values, treating an equality-check failure as a difference."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False


class _SettlingNode(Protocol):
    """A named derived value that can participate in cycle detection."""

    _label: str


_SETTLING: ContextVar[tuple[_SettlingNode, ...]] = ContextVar("squid_reactivity_settling", default=())
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
    stack = _SETTLING.get()
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
    with _SETTLING.set((*_SETTLING.get(), node)):
        yield


_EPOCH = 0
"""Advance when state is staged, published, restored, or invalidated.

Computed values use this counter to skip dependency checks when no state has changed.
"""


def _bump_epoch() -> None:
    global _EPOCH
    _EPOCH += 1


@contextmanager
def untracked() -> Iterator[None]:
    """Read state without subscribing to it.

    For code that reads to decide what to do rather than to derive a value from it. Action
    handlers already run outside any consumer, so this is for a computed that deliberately
    samples something it does not want to recompute for.
    """
    with _DEPENDENCY_COLLECTOR.set(None):
        yield


@dataclass(slots=True)
class Dependencies:
    """Record dependencies read by one render or computation.

    Addresses are collected through computed values and resources as well as
    direct cell reads. Shared state is tracked automatically; watch adds a
    dependency on a topic that has no value to read.
    """

    sources: dict[_ReactiveSource, int] = field(default_factory=dict)

    def current(self) -> bool:
        """Whether every directly observed reactive source still yields the seen version.

        Settling a computed here preserves its backdating contract: an upstream write that
        recomputes to the same value leaves the computed's version unchanged and therefore does
        not invalidate a cached pure projection.
        """
        return all(source.settle() == seen for source, seen in tuple(self.sources.items()))

    def addresses(self) -> tuple[Topic | CellAddress, ...]:
        """Every addressed cell this run reached, deduplicated, in read order.

        Anything with sources of its own is walked rather than re-run -- a cached computed, a
        settled resource. It did not read its sources again, but a reader that used its value
        still depends on every one of them, so a topic watched two loaders down is still this
        render's dependency. One that carries an address of its own contributes that too.
        """
        found: list[Topic | CellAddress] = []
        seen: set[int] = set()

        def walk(sources: Iterable[_ReactiveSource]) -> None:
            for source in sources:
                identity = id(source)
                if identity in seen:
                    continue
                seen.add(identity)
                address = source.address
                if address is not None:
                    found.append(address)
                walk(source.dependencies())

        walk(self.sources)
        return tuple(found)


@contextmanager
def capture_reads() -> Iterator[Dependencies]:
    """Collect direct reactive dependencies without changing write permissions.

    Nested captures record their own reads. Computed values and resources remain
    direct dependencies; their upstream notification addresses are resolved on demand.
    """
    dependencies = Dependencies()
    with _DEPENDENCY_COLLECTOR.set(dependencies):
        yield dependencies


@contextmanager
def observe_reads() -> Iterator[Dependencies]:
    """Collect dependencies of a read-only computation."""
    with read_only(), capture_reads() as dependencies:
        yield dependencies


def addresses(read: Callable[[], object]) -> tuple[Topic | CellAddress, ...]:
    """Return the addresses read by a callable, raising if none are found.

    For example, ``addresses(lambda: preferences.theme)`` returns the address
    of that field. Reads through computed values include their source
    addresses. The callable runs as a read-only computation.
    """
    with observe_reads() as observation:
        read()
    found = observation.addresses()
    if not found:
        message = (
            "addresses() read no reactive state: the callable must read at least one state() "
            "field on a Reactive object, directly or through a computed."
        )
        raise ValueError(message)
    return found
