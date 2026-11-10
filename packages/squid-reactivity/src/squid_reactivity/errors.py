"""Named failures shared by reactive models, dependency tracking, and transactions."""

from collections.abc import Sequence

from squid_reactivity.actions import (
    ConflictDetail,
)


class ReactivityError(Exception):
    """Base class for the package's named reactive-state errors.

    Subclasses also inherit standard exception types such as RuntimeError,
    ValueError, or LookupError so callers can catch either category.
    """


class ReactiveWriteError(ReactivityError, RuntimeError):
    """A state mutation was attempted inside a read-only action."""


class ActionValidationError(ReactivityError, ValueError):
    """An admitted action failed application validation before publication."""


class FrameworkIntegrityError(ReactivityError, RuntimeError):
    """An infallible publication adapter failed after the local commit point."""


class FreshActionError(ReactivityError, RuntimeError):
    """A distinct action was requested after its admitting transaction staged work."""


class UndeclaredStateError(ReactivityError, RuntimeError):
    """An attribute that is not declared state was written inside a transaction."""


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


class StaleReactiveContextError(ReactivityError, RuntimeError):
    """A write used a finished transaction or came from a different task.

    Child tasks inherit the transaction context, but only its owning task may
    stage writes. Reads from child tasks are allowed. Once the transaction closes,
    reads ignore its staged values and use the committed cells.
    """


class ReactiveConflictError(ReactivityError, RuntimeError):
    """A required shared-state version changed before commit.

    The action discards its staged changes. The error is raised when the
    transaction context exits, so catch it outside the with block.
    """

    def __init__(self, detail: ConflictDetail, message: str) -> None:
        self.detail = detail
        super().__init__(message)
