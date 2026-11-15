"""Internal transaction context and rules for reading or writing reactive state.

Cells and transaction publication share this module without importing each other's
implementations. The active transaction type is needed only for annotations.
"""

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from threading import current_thread
from typing import TYPE_CHECKING, Protocol

from squid_reactivity._addresses import SubscriptionSource
from squid_reactivity.errors import ReactiveWriteError, StaleReactiveContextError

if TYPE_CHECKING:
    from squid_reactivity.transactions._engine import _TransactionEngine


class ReactiveStateOwner(SubscriptionSource, Protocol):
    """A subscription source that receives commit and rollback notifications.

    State descriptors also require an instance dictionary for per-field storage.
    """

    __slots__ = ()

    def _state_changed(self, names: frozenset[str]) -> None: ...

    def _state_rolled_back(self) -> None: ...


_CURRENT_TRANSACTION: ContextVar[_TransactionEngine | None] = ContextVar("squid_reactivity_transaction", default=None)
"""Transaction overlay used by field descriptors during staging.

Access from another thread is rejected. Only its owning task may write.
Child tasks inherit the same transaction object;
closed overlays are ignored for reads and rejected for writes. Publication resets
this slot before notifying consumers, so callbacks cannot join a finished overlay.
"""


def _current_task() -> object | None:
    """Return the current asyncio task, or None when no event loop is running."""
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _current_transaction() -> _TransactionEngine | None:
    """Read the inherited overlay, rejecting access from another thread."""
    current = _CURRENT_TRANSACTION.get()
    if current is not None and current.owner_thread is not current_thread():
        message = (
            "transaction context belongs to another thread; "
            "start independent reactive work in a thread with context=Context()"
        )
        raise StaleReactiveContextError(message)
    return current


def _active() -> _TransactionEngine | None:
    """Return the open transaction used for reads, ignoring a finished context.

    A child task may retain a transaction after its context exits. Reads then
    use committed state, while writes through that context are rejected.
    """
    current = _current_transaction()
    return None if current is None or current.closed else current


_READ_ONLY: ContextVar[object | None] = ContextVar("squid_reactivity_read_only", default=None)
"""Identity of the current computation's write restriction, or None when unrestricted.

A fresh identity permits initialization only for objects born in this exact scope.
A boolean would incorrectly allow a nested computation to mutate outer-scope objects.
"""


@contextmanager
def read_only() -> Iterator[None]:
    """Reject reactive writes, except initialization of objects created in this scope.

    Nested scopes cannot initialize objects belonging to an outer scope. Dependency
    capture is independent: use capture_reads() to collect reads as well.
    """
    with _READ_ONLY.set(object()):
        yield


@contextmanager
def block_writes(reason: str) -> Iterator[None]:
    """Temporarily reject writes through the current transaction.

    The transaction retains its staged work and resumes normal writes when the scope
    exits. With no transaction, this context does nothing. Unlike read_only(), it does
    not establish a general restriction on reactive writes outside transactions.

    Undo integrations use this while running external inverses, before restoring fields
    whose values would otherwise overwrite additional staged changes.
    """
    current = _current_transaction()
    if current is None:
        yield
        return
    previous = current.write_block
    current.write_block = reason
    try:
        yield
    finally:
        current.write_block = previous


def _check_write(owner: ReactiveStateOwner, name: str) -> None:
    scope = _READ_ONLY.get()
    if scope is not None and owner.__dict__.get("_reactive_initialization") is not scope:
        message = (
            f"{type(owner).__name__}.{name} was written during a read-only computation. "
            "Initialize defaults with state(factory=...), derive values with computed, "
            "and update existing state outside the computation."
        )
        raise ReactiveWriteError(message)


class _ReadConsistency(Enum):
    DEFAULT = "default"
    STRONG = "strong"
    RELAXED = "relaxed"


_READ_CONSISTENCY: ContextVar[_ReadConsistency] = ContextVar(
    "squid_reactivity_read_consistency", default=_ReadConsistency.DEFAULT
)
"""Commit preconditions for reads, independent of reactive dependency capture.

Relaxed scopes override strong scopes in either nesting order. Each scope restores
its caller's policy on exit; children inherit the policy present when spawned.
"""


@contextmanager
def relaxed_read() -> Iterator[None]:
    """Read without adding transaction version conditions.

    This suppresses both strong-read conditions and ordinary read-and-write conditions
    for reads inside the scope. It does not disable reactive dependency capture.
    """
    with _READ_CONSISTENCY.set(_ReadConsistency.RELAXED):
        yield


@contextmanager
def strong_read() -> Iterator[None]:
    """Require versions read in this scope to remain unchanged when committing changes.

    Ordinary reads are checked only when the transaction also writes the same cell.
    Use strong_read() when a decision reads one field and writes another: the first
    field must still have the version that informed the decision. Transactions with
    no writes or participants skip version validation.

    An enclosing or nested relaxed_read() suppresses these conditions. Dependency
    capture and subscriptions are unaffected.
    """
    policy = _READ_CONSISTENCY.get()
    if policy is not _ReadConsistency.RELAXED:
        policy = _ReadConsistency.STRONG
    with _READ_CONSISTENCY.set(policy):
        yield


_INTERLEAVER: ContextVar[Callable[[str], None] | None] = ContextVar(
    "squid_reactivity_deterministic_interleaver", default=None
)
"""Test-only checkpoint callback installed by InterleavingHarness.

It inherits into child tasks like transaction state.
"""


def _checkpoint(name: str) -> None:
    interleaver = _INTERLEAVER.get()
    if interleaver is not None:
        interleaver(name)
