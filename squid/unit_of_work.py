"""Application-owned transactions and their bound repositories."""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self


class UnitOfWork(Protocol):
    """Group repository changes in a transaction that the caller must commit.

    Enter the scope with `async with` and use it from one task. Call `commit()`
    to save changes; leaving without committing rolls them back. After commit,
    the scope no longer accepts repository calls, even before the context exits.
    Loaded entities detach when the scope closes, so later edits are not saved.
    """

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None: ...

    async def commit(self) -> None: ...


type UnitOfWorkFactory[UowT: UnitOfWork] = Callable[[], UowT]
