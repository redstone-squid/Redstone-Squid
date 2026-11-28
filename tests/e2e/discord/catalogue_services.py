from typing import override

from squid.core.actors import ActingContext
from squid.core.builds.application import BuildQueryService
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import Build, DoorBuild
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.application.results import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchPage
from squid.core.search.domain import SearchRequest, SearchScope


class CatalogueSearch(SearchService):
    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    @override
    async def fields(self, *, context: ActingContext) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY

    @override
    async def search(self, request: SearchRequest, *, context: ActingContext) -> SearchPage:
        self.requests.append(request)
        if request.scope is SearchScope.RECORDS:
            hit = RecordSearchHit("7", "Smallest spiral door", None, 5, "Aurora", "smallest", "all_time")
        elif request.scope is SearchScope.METADATA:
            hit = MetadataSearchHit("9", "Spiral piston layout", "pattern")
        else:
            assert request.scope is SearchScope.BUILDS
            hit = BuildSearchHit("73", "Five-wide door", "approved")
        return SearchPage((hit,), total=1, next=None, prev=None)


class CatalogueQueries(BuildQueryService):
    def __init__(self) -> None:
        self.pending_build = DoorBuild(id=73, submission_status=BuildStatus.PENDING)

    @override
    async def get(self, build_id: int, *, context: ActingContext) -> Build | None:
        assert build_id == self.pending_build.id
        return self.pending_build
