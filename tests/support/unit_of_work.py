"""Common scope behavior for service test doubles."""

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from types import TracebackType
from typing import Self

from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass
class CommittedState[StateT]:
    """Committed data shared by successive fake transactions."""

    value: StateT
    clone: Callable[[StateT], StateT] = deepcopy
    """Copy values for a transaction or commit; mapped entities can supply their own copier."""


class FakeUnitOfWork[StateT]:
    """Work on a copy and publish a separate snapshot only when committed."""

    def __init__(self, committed: CommittedState[StateT]) -> None:
        self._committed = committed
        self.state = committed.clone(committed.value)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        pass

    async def commit(self) -> None:
        self._committed.value = self._committed.clone(self.state)


async def transact[UowT: UnitOfWork, ResultT](
    factory: UnitOfWorkFactory[UowT], operation: Callable[[UowT], Awaitable[ResultT]], *, commit: bool = False
) -> ResultT:
    """Run a repository operation in an explicit read or write transaction."""
    async with factory() as uow:
        result = await operation(uow)
        if commit:
            await uow.commit()
        return result


class PublishingFakeUnitOfWork[StateT](FakeUnitOfWork[StateT]):
    """Publish committed snapshots into the fixture object assertions inspect."""

    def __init__(self, state: StateT) -> None:
        self._published = state
        super().__init__(CommittedState(state))

    async def commit(self) -> None:
        await super().commit()
        vars(self._published).update(vars(self._committed.value))
