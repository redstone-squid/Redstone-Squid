"""Public build summary readers for services that attach holder cards."""

from collections.abc import Sequence
from unittest.mock import Mock

from squid.core.actors import ActingContext
from squid.core.builds.application.queries import BuildQueryService, PublicBuildSummary
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.unit_of_work import UnitOfWorkFactory


class NoPublicBuilds:
    """A catalogue in which no build is public."""

    async def get_public_summaries(
        self, build_ids: Sequence[int], *, context: ActingContext
    ) -> Sequence[PublicBuildSummary]:
        return ()


def public_builds(uow_factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork]) -> BuildQueryService:
    """Read summaries from the database; semantic build search is never used."""
    return BuildQueryService(Mock(), PermissionService(uow_factory=uow_factory), uow_factory=uow_factory)
