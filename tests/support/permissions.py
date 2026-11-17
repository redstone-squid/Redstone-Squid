"""Read-only permission scopes for service and transport tests."""

from functools import partial
from typing import override

from squid.permissions.application.ports import PermissionRepository, PermissionUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork


class ReadOnlyPermissionUnitOfWork(FakeUnitOfWork[PermissionRepository]):
    @property
    def permissions(self) -> PermissionRepository:
        return self.state

    @override
    async def commit(self) -> None:
        raise AssertionError("Permission reads must not commit.")


def permission_uow_factory(store: PermissionRepository) -> UnitOfWorkFactory[PermissionUnitOfWork]:
    # Keep load counters visible to tests; this port exposes only reads.
    return partial(ReadOnlyPermissionUnitOfWork, CommittedState(store, clone=lambda value: value))
