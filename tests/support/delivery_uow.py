"""Transaction snapshots for delivery-service fixtures."""

from copy import copy

from tests.support.unit_of_work import PublishingFakeUnitOfWork


class DomainEventUnitOfWork[RepositoryT](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT) -> None:
        super().__init__(repository, clone=copy)

    @property
    def domain_events(self) -> RepositoryT:
        return self.state


class ReconciliationUnitOfWork[RepositoryT](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT) -> None:
        super().__init__(repository, clone=copy)

    @property
    def reconciliation(self) -> RepositoryT:
        return self.state


class NotificationUnitOfWork[RepositoryT](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT) -> None:
        super().__init__(repository, clone=copy)

    @property
    def notifications(self) -> RepositoryT:
        return self.state
