"""Capture reactive dependencies and check whether their versions still match.

A capture records the sources read by a computation. Dependencies can validate
those versions or find the addresses a consumer needs to subscribe to.
"""

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from squid_reactivity.transactions._state import read_only

if TYPE_CHECKING:
    from squid_reactivity.topics import DependencyAddress


class _ReactiveSource(Protocol):
    """A versioned value with explicit upstream dependencies and a notification address.

    Validation may change the dependency graph without changing the value's version.
    Address traversal therefore follows the live graph after validation.
    """

    __slots__ = ()

    @property
    def address(self) -> DependencyAddress | None: ...

    def dependencies(self) -> Iterable[_ReactiveSource]: ...

    def validated_version(self) -> int:
        """Validate dependencies synchronously and return the resulting version.

        Computed values may recompute; resources may become pending. This method
        never starts asynchronous work. Cells and signals simply return their
        current version, including staged changes where applicable.
        """
        ...


class _DependencyCollector(Protocol):
    """Receive source versions observed by a computation or asynchronous load."""

    __slots__ = ()

    sources: dict[_ReactiveSource, int]


_DEPENDENCY_COLLECTOR: ContextVar[_DependencyCollector | None] = ContextVar(
    "squid_reactivity_dependency_collector", default=None
)
"""Destination for source versions read by the active computation or resource load.

Capture scopes and loaders install their own collector; untracked clears it temporarily.
Child tasks inherit the same collector object. Operation loaders explicitly clear it so
awaiting an operation does not make its inputs dependencies of the awaiting computation.
"""


@dataclass(slots=True)
class Dependencies(_DependencyCollector):
    """Record sources and versions read within one dependency-capture scope.

    Computed values and resources remain direct dependencies rather than being flattened
    into their inputs. Address discovery traverses those inputs to find subscription
    sources. watch() adds a topic dependency even though that topic has no stored value.
    """

    sources: dict[_ReactiveSource, int] = field(default_factory=dict)

    def current(self) -> bool:
        """Whether every directly observed reactive source still yields the seen version.

        Validating a computed here preserves its version when its value is unchanged: an upstream write that
        recomputes to the same value leaves the computed's version unchanged and therefore does
        not invalidate a cached pure projection.
        """
        return all(source.validated_version() == seen for source, seen in tuple(self.sources.items()))

    def addresses(self) -> tuple[DependencyAddress, ...]:
        """Every addressed cell this run reached, deduplicated, in read order.

        Anything with sources of its own is walked rather than re-run -- a cached computed, a
        settled resource. It did not read its sources again, but a reader that used its value
        still depends on every one of them, so a topic watched two loaders down is still this
        consumer's dependency. One that carries an address of its own contributes that too.
        """
        found: list[DependencyAddress] = []
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


@contextmanager
def untracked() -> Iterator[None]:
    """Read reactive values without adding them to the surrounding dependency capture.

    Use this when a computation needs to sample a value without depending on its changes.
    Transaction conflict tracking is independent and remains active.
    """
    with _DEPENDENCY_COLLECTOR.set(None):
        yield


def addresses(read: Callable[[], object]) -> tuple[DependencyAddress, ...]:
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
