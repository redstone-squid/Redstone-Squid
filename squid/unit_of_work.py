"""Application-owned transactions and their bound repositories."""

from collections.abc import Callable
from types import TracebackType
from typing import ClassVar, Protocol, Self


class EventFact(Protocol):
    """A business transition ready for transactional publication."""

    event_type: ClassVar[str]
    aggregate_kind: ClassVar[str]
    schema_version: ClassVar[int]

    @property
    def aggregate_id(self) -> int: ...

    def payload(self) -> dict[str, object]: ...


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

    async def emit(self, event: EventFact) -> None: ...

    async def commit(self) -> None: ...


type UnitOfWorkFactory[UowT: UnitOfWork] = Callable[[], UowT]
