"""Commit-visible scopes for schematic worker fakes."""

from copy import copy

from squid.schematics.application.jobs import SchematicJobRepository
from squid.schematics.application.render_jobs import SchematicRenderJobRepository
from tests.support.unit_of_work import PublishingFakeUnitOfWork


class JobScope[RepositoryT: SchematicJobRepository](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT) -> None:
        super().__init__(repository, clone=copy)

    @property
    def schematic_jobs(self) -> RepositoryT:
        return self.state


class RenderScope[RepositoryT: SchematicRenderJobRepository](PublishingFakeUnitOfWork[RepositoryT]):
    def __init__(self, repository: RepositoryT) -> None:
        super().__init__(repository, clone=copy)

    @property
    def schematic_renders(self) -> RepositoryT:
        return self.state
