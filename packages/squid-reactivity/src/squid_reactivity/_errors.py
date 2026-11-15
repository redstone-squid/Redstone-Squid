"""Named failures shared by reactive models, dependency tracking, and transactions."""

from collections.abc import Sequence

from squid_reactivity.transactions._results import (
    ConflictDetail,
)


class ReactivityError(Exception):
    """Base class for the package's named reactive-state errors.

    Subclasses also inherit standard exception types such as RuntimeError,
    ValueError, or LookupError so callers can catch either category.
    """


class ReactiveWriteError(ReactivityError, RuntimeError):
    """A reactive write violated a computation, transaction, or callback write restriction."""


class UndeclaredStateError(ReactivityError, RuntimeError):
    """An attribute that is not declared state was written inside a transaction."""


class StaleReactiveContextError(ReactivityError, RuntimeError):
    """Reactive access used a transaction outside its allowed lifetime or execution context.

    Only the task that opened a transaction may stage writes. Child tasks on the same
    thread may read its staged values; after closure, their reads use committed state
    and their writes fail. Access through a context inherited by another thread fails.
    """


class ReactiveCycleError(ReactivityError, RuntimeError):
    """A computed value or resource depends on itself, directly or indirectly.

    The path includes the full cycle, for example ``a -> b -> a``. Dependencies
    can cross between computed values and asynchronous resource loaders.
    """

    def __init__(self, path: Sequence[str]) -> None:
        self.path = tuple(path)
        joined = " -> ".join(self.path)
        message = f"cycle: {joined}. Nothing in that ring can settle, because each waits on the next."
        super().__init__(message)


class ReactiveConflictError(ReactivityError, RuntimeError):
    """A required shared-state version changed before commit.

    The transaction discards its staged changes. The error is raised when the
    transaction context exits, so catch it outside the with block.
    """

    def __init__(self, detail: ConflictDetail, message: str) -> None:
        self.detail = detail
        super().__init__(message)


class FreshTransactionError(ReactivityError, RuntimeError):
    """An independent transaction was requested after the enclosing transaction staged work."""


class TransactionValidationError(ReactivityError, ValueError):
    """Application validation rejected a transaction before publication."""


class FrameworkIntegrityError(ReactivityError, RuntimeError):
    """An infallible publication adapter failed after the local commit point."""
