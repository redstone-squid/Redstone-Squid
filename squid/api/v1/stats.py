"""Catalogue-wide counts."""

from litestar import get
from litestar.di import Provide
from pydantic import BaseModel, ConfigDict

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import CatalogueStatistics, CurrentCaller, get_catalogue_stats
from squid.api.errors import responses
from squid.core.catalogue.application import CatalogueStats


class CatalogueStatsDetail(BaseModel):
    """Headline counts for the home page."""

    model_config = ConfigDict(extra="forbid")

    builds: int
    """Approved builds, the total of the default build list."""
    records: int
    """Current record results, the total of the default record list."""
    creators: int
    """Distinct creators and designers credited on approved builds; a claimed account counts once."""

    @classmethod
    def from_domain(cls, stats: CatalogueStats) -> CatalogueStatsDetail:
        return cls(builds=stats.builds, records=stats.records, creators=stats.creators)


@get(
    "/stats",
    responses=responses(503),
    operation_id="catalogue_stats",
    tags=["catalogue"],
    dependencies={"statistics": Provide(get_catalogue_stats, sync_to_thread=False)},
    response_headers={"Cache-Control": "public, max-age=300"},
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def catalogue_stats(statistics: CatalogueStatistics, caller: CurrentCaller) -> CatalogueStatsDetail:
    """Count approved builds, current records, and credited creators."""
    return CatalogueStatsDetail.from_domain(await statistics.stats(context=caller.context()))
