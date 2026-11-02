"""Application-owned transactions and their bound repositories."""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self


class UnitOfWork(Protocol):
    """Track mapped changes until commit; leaving without commit rolls back.

    Each scope belongs to one task. Commit is terminal, and repositories may only
    be used inside an active scope. Successfully committed entities are detached
    when the scope closes; entities from a rolled-back scope should be discarded.
    """

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None: ...

    async def commit(self) -> None: ...


type UnitOfWorkFactory[UowT: UnitOfWork] = Callable[[], UowT]
