"""Common scope behavior for service test doubles."""

from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from types import TracebackType
from typing import Self

from squid.builds.history import BuildEditActor, HistoryOperation
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass
class CommittedState[StateT]:
    """State and copy policy shared by successive fake transactions.

    The default deep copy isolates nested mutations. A shallow or identity copier shares
    nested objects, so tests using one must not assume full rollback isolation.
    """

    value: StateT
    clone: Callable[[StateT], StateT] = deepcopy
    """Copy values for a transaction or commit; mapped entities can supply their own copier."""


class FakeBuildEditing:
    """Attribution scope for service tests; database tests exercise recording itself."""

    def editing(
        self,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "edit",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> nullcontext[None]:
        return nullcontext()


class FakeUnitOfWork[StateT]:
    """Give a service a working copy of state and publish its changes when it commits.

    The copy policy comes from CommittedState and defaults to a deep copy.
    Exiting does not commit or restore the working copy. Uncommitted work is discarded only
    by dropping the scope, and committed changes survive a later exception. This fake does
    not model locking, concurrent writers, or a database transaction's closed-state checks.
    """

    def __init__(self, committed: CommittedState[StateT]) -> None:
        self._committed = committed
        self.state = committed.clone(committed.value)

    @property
    def builds(self) -> FakeBuildEditing:
        return FakeBuildEditing()

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
    """Copy committed attributes into the original fixture object so assertions see them.

    Support dataclass instances or objects with __dict__. Attributes are assigned or updated;
    keys deleted from a regular object's working copy are not removed from the fixture.
    """

    def __init__(self, state: StateT, *, clone: Callable[[StateT], StateT] = deepcopy) -> None:
        self._published = state
        super().__init__(CommittedState(state, clone=clone))

    async def commit(self) -> None:
        await super().commit()
        snapshot = self._committed.value
        if is_dataclass(snapshot) and not isinstance(snapshot, type):
            for field in fields(snapshot):
                setattr(self._published, field.name, getattr(snapshot, field.name))
        else:
            vars(self._published).update(vars(snapshot))
