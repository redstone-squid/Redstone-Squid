"""Track versioned dependencies and the read context shared by reactive consumers.

Cells consult the active transaction's overlay, while computed values and async
resources collect their sources through the same consumer context. This module
owns those contexts and invalidation epochs; transaction publication and model
descriptors live in their respective modules.
"""

import asyncio
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from squid_reactivity.addresses import CellAddress, publish

if TYPE_CHECKING:
    from squid_reactivity.transactions import _Transaction

from squid_reactivity.errors import (
    ReactiveCycleError,
    ReactiveWriteError,
)

_MISSING = object()
"""A cell that has never been assigned. Distinct from ``None``, which is a real value."""


class ReactiveOwner(Protocol):
    """Hooks called when an owner's state commits or rolls back.

    State descriptors also require an instance dictionary for cell storage.
    """

    def _state_changed(self, names: frozenset[str]) -> None: ...

    def _state_rolled_back(self) -> None: ...


def _equal(left: object, right: object) -> bool:
    """Compare values, treating an equality-check failure as a difference."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False


_CURRENT: ContextVar[_Transaction | None] = ContextVar("squid_reactivity_transaction", default=None)
_RELAXED_READS: ContextVar[int] = ContextVar("squid_reactivity_relaxed_reads", default=0)
_STRONG_READS: ContextVar[int] = ContextVar("squid_reactivity_strong_reads", default=0)
_INTERLEAVER: ContextVar[Callable[[str], None] | None] = ContextVar(
    "squid_reactivity_deterministic_interleaver", default=None
)
_INTERLEAVER_USERS: int = 0


def _checkpoint(name: str) -> None:
    if _INTERLEAVER_USERS == 0:
        return
    interleaver = _INTERLEAVER.get()
    if interleaver is not None:
        interleaver(name)


def _current_task() -> object | None:
    """Return the current asyncio task, or None when no event loop is running."""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _active() -> _Transaction | None:
    """Return the open transaction used for reads, ignoring a finished context.

    A child task may retain a transaction after its context exits. Reads then
    use committed state, while writes through that context are rejected.
    """
    current = _CURRENT.get()
    return None if current is None or current.closed else current


class _Cell:
    """Store one field's value, version, and optional publication address.

    Reactive fields, including component fields, have CellAddress values. Topics
    use cells with Topic addresses and no payload. Addressed reads can
    be checked for conflicts and collected as render subscriptions.
    """

    __slots__ = ("__weakref__", "address", "identity", "value", "version")

    def __init__(self, value: Any = _MISSING, version: int = 0, address: Any = None) -> None:
        self.identity = uuid.uuid7()
        self.value = value
        self.version = version
        self.address = address

    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    def settle(self) -> int:
        """Return the version a reader should compare against. A cell is always settled."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                return entry.version
        return self.version

    def track(self, version: int) -> None:
        """Record a read of this cell at `version` with whatever is consuming it."""
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = version

    def read(self) -> Any:
        """Return the value this reader should see, staged write included, and record it."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                # A staged read does not observe committed state, so it adds no
                # commit-time version precondition.
                self.track(entry.version)
                return entry.value
            if self.address is not None and _RELAXED_READS.get() == 0:
                current.observe(self, strong=_STRONG_READS.get() > 0)
        self.track(self.version)
        return self.value

    def write(self, value: Any) -> None:
        self.value = value
        self.touch()

    def touch(self) -> None:
        """Advance the version without replacing the value."""
        self.version += 1
        _bump_epoch()


def _staged_value(cell: _Cell) -> Any:
    """This action's pending value for `cell`, or `_MISSING` if it has not written one."""
    current = _active()
    if current is None:
        return _MISSING
    entry = current.staged(cell)
    return _MISSING if entry is None else entry.value


class _ReactiveSource(Protocol):
    """A versioned value with explicit upstream dependencies and a notification address.

    Settling may change the upstream graph without changing the value's version.
    Address traversal therefore follows the live graph after validation.
    """

    @property
    def address(self) -> Any: ...

    def dependencies(self) -> Iterable[_ReactiveSource]: ...

    def settle(self) -> int: ...


class _Consumer(Protocol):
    """Collect dependencies read by a computed value, resource load, or render."""

    sources: dict[_ReactiveSource, int]


_CONSUMER: ContextVar[_Consumer | None] = ContextVar("squid_reactivity_consumer", default=None)


class _Signal:
    """Observe framework-managed changes that do not belong to an action transaction.

    Operation progress and history bookkeeping publish immediately. Keeping their
    versions in the ordinary dependency graph makes computed values and renders
    follow them without assigning either a component owner.
    """

    def __init__(self) -> None:
        self.address = CellAddress(self, "revision")
        self.version = 0

    def track(self) -> None:
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = self.version

    def dependencies(self) -> tuple[_ReactiveSource, ...]:
        return ()

    def settle(self) -> int:
        return self.version

    def notify(self) -> None:
        self.version += 1
        _bump_epoch()
        publish(self.address)


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


_READ_ONLY: ContextVar[object | None] = ContextVar("squid_reactivity_read_only", default=None)


@contextmanager
def read_only() -> Iterator[None]:
    """Reject reactive writes, except initialization of objects created in this scope.

    Nested scopes cannot initialize objects belonging to an outer scope. Dependency
    capture is independent: use capture_reads() to collect reads as well.
    """
    with _READ_ONLY.set(object()):
        yield


def _check_write(owner: ReactiveOwner, name: str) -> None:
    scope = _READ_ONLY.get()
    if scope is not None and owner.__dict__.get("_reactive_initialization") is not scope:
        message = (
            f"{type(owner).__name__}.{name} was written during a read-only computation. "
            "Initialize defaults with state(factory=...), derive values with computed, "
            "and update existing state outside the computation."
        )
        raise ReactiveWriteError(message)


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
    with _CONSUMER.set(None):
        yield


@contextmanager
def relaxed_read() -> Iterator[None]:
    """Read reactive state without adding a commit-time consistency precondition.

    Only reads a precondition would otherwise cover are affected: a cell this action also
    writes, and any read taken inside :func:`strong_read`. Unlike :func:`untracked`, this
    does not change reactive dependency tracking.
    """
    with _RELAXED_READS.set(_RELAXED_READS.get() + 1):
        yield


@contextmanager
def strong_read() -> Iterator[None]:
    """Require every reactive read taken inside to still hold its version at commit.

    The upgrade an action opts into when it branches on reactive state it does not write --
    read A, write B, and by default nothing says A was still true. Wrap the whole handler
    body to make one action serializable over what it read; wrap one read to guard just that
    one. :func:`relaxed_read` nested inside opts a read back out.
    """
    with _STRONG_READS.set(_STRONG_READS.get() + 1):
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

    def addresses(self) -> tuple[Any, ...]:
        """Every addressed cell this run reached, deduplicated, in read order.

        Anything with sources of its own is walked rather than re-run -- a cached computed, a
        settled resource. It did not read its sources again, but a reader that used its value
        still depends on every one of them, so a topic watched two loaders down is still this
        render's dependency. One that carries an address of its own contributes that too.
        """
        found: list[Any] = []
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
    with _CONSUMER.set(dependencies):
        yield dependencies


@contextmanager
def observe_reads() -> Iterator[Dependencies]:
    """Collect dependencies of a read-only computation."""
    with read_only(), capture_reads() as dependencies:
        yield dependencies


def addresses(read: Callable[[], object]) -> tuple[Any, ...]:
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
