"""Read-only permission scopes for service and transport tests."""

from functools import partial

from squid.permissions.application.ports import PermissionStore, PermissionUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class ReadOnlyPermissionUnitOfWork(FakeUnitOfWork[PermissionStore]):
    @property
    def permissions(self) -> PermissionStore:
        return self.state

    async def commit(self) -> None:
        raise AssertionError("Permission reads must not commit.")


def permission_uow_factory(store: PermissionStore) -> UnitOfWorkFactory[PermissionUnitOfWork]:
    # Keep load counters visible to tests; this port exposes only reads.
    return partial(ReadOnlyPermissionUnitOfWork, CommittedState(store, clone=lambda value: value))
