"""Internal transaction context and rules for reading or writing reactive state.

Cells and transaction publication share this module without importing each other's
implementations. The active transaction type is needed only for annotations.
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

from squid_reactivity.addresses import SubscriptionSource
from squid_reactivity.errors import ReactiveWriteError
from squid_reactivity.transaction_context import TransactionContext

if TYPE_CHECKING:
    from squid_reactivity.transactions import _Transaction


class ReactiveStateOwner(SubscriptionSource, Protocol):
    """A subscription source whose state participates in commit and rollback.

    State descriptors also require an instance dictionary for cell storage.
    """

    def _state_changed(self, names: frozenset[str]) -> None: ...

    def _state_rolled_back(self) -> None: ...


_CURRENT_TRANSACTION: ContextVar[_Transaction | None] = ContextVar("squid_reactivity_transaction", default=None)
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
    current = _CURRENT_TRANSACTION.get()
    return None if current is None or current.closed else current


_READ_ONLY: ContextVar[object | None] = ContextVar("squid_reactivity_read_only", default=None)


@contextmanager
def read_only() -> Iterator[None]:
    """Reject reactive writes, except initialization of objects created in this scope.

    Nested scopes cannot initialize objects belonging to an outer scope. Dependency
    capture is independent: use capture_reads() to collect reads as well.
    """
    with _READ_ONLY.set(object()):
        yield


def _check_write(owner: ReactiveStateOwner, name: str) -> None:
    scope = _READ_ONLY.get()
    if scope is not None and owner.__dict__.get("_reactive_initialization") is not scope:
        message = (
            f"{type(owner).__name__}.{name} was written during a read-only computation. "
            "Initialize defaults with state(factory=...), derive values with computed, "
            "and update existing state outside the computation."
        )
        raise ReactiveWriteError(message)


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


def current_transaction_context() -> TransactionContext | None:
    """Return the active transaction's metadata, or None outside its staging phase.

    Metadata lives on the transaction itself. Publication and result callbacks use
    captured causality instead; an inherited, closed transaction is not active.
    """
    current = _active()
    return None if current is None else current.context
