"""Draft service scopes with commit-visible repository state."""

from squid.permissions.application.ports import PermissionStore
from squid.submissions.application.drafts import DraftRepository
from tests.support.unit_of_work import PublishingFakeUnitOfWork


class DraftScope[RepositoryT: DraftRepository](PublishingFakeUnitOfWork[RepositoryT]):
    @property
    def submission_drafts(self) -> RepositoryT:
        return self.state

    @property
    def permissions(self) -> PermissionStore:
        raise AssertionError("These draft tests supply their own permission policy.")
