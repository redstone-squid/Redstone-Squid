"""Search discovery and cross-resource matching routes."""

import logging
from collections.abc import Sequence
from typing import Annotated

from litestar import Router, get
from litestar.di import Provide
from litestar.dto import DataclassDTO, DTOConfig
from litestar.params import QueryParameter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import BuildQueries, Search, get_build_queries, get_search
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, Page, PageAnchor, PageSizeParam
from squid.api.v1.schemas.builds import BuildSummary
from squid.api.v1.schemas.search import (
    BuildSearchResult,
    MetadataSearchEntry,
    MetadataSearchResult,
    RecordSearchEntry,
    RecordSearchResult,
    SearchResult,
    SearchSuggestions,
)
from squid.builds.application import BuildQueryService
from squid.core.errors import DataIntegrityError
from squid.search.application.fields import FieldDefinition
from squid.search.domain import SearchHit, SearchMode, SearchRequest, SearchScope, SearchSort

logger = logging.getLogger(__name__)

# Public search always restricts results to confirmed builds. This separate filter
# also applies when the query itself asks for another status.
PUBLIC_SEARCH_STATUSES = frozenset({"confirmed"})


class PublicSearchFieldDTO(DataclassDTO[FieldDefinition]):
    """Describe supported search syntax without exposing storage keys or coercion settings."""

    __schema_name__ = "SearchField"
    config = DTOConfig(
        include={"name", "value_type", "supports_range", "supports_sort", "aliases"},
        rename_fields={"value_type": "type"},
    )


@get(
    "/fields",
    responses=responses(503),
    operation_id="search_fields_list",
    return_dto=PublicSearchFieldDTO,
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_search_fields(search_service: Search) -> tuple[FieldDefinition, ...]:
    """Publish the effective allowlisted query-field registry."""
    registry = await search_service.fields()
    return registry.definitions


@get(
    "/suggest",
    responses=responses(400, 422, 503),
    operation_id="search_terms_suggest",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def suggest_terms(
    search_service: Search,
    q: Annotated[str, QueryParameter(max_length=1_000)],
    limit: Annotated[int, QueryParameter(ge=1, le=25)] = 5,
) -> SearchSuggestions:
    """Suggest indexed terms completing a valid query."""
    return SearchSuggestions(suggestions=list(await search_service.suggest(q, limit=limit)))


@get(
    "/",
    responses=responses(400, 422, 503),
    operation_id="search_execute",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def search(
    search_service: Search,
    build_queries: BuildQueries,
    q: Annotated[str, QueryParameter(max_length=1_000)],
    search_scope: Annotated[SearchScope, QueryParameter(name="scope")] = SearchScope.ALL,
    sort: Annotated[str | None, QueryParameter(max_length=80)] = None,
    page_size: PageSizeParam = 20,
    offset: OffsetParam = None,
    group_by_build: Annotated[
        bool, QueryParameter(description="Combine matching records and builds before pagination.")
    ] = False,
) -> Page[SearchResult]:
    """Match builds, computed records, and taxonomy entries in one ranked page."""
    result = await search_service.search(
        SearchRequest(
            query=q,
            scope=search_scope,
            mode=SearchMode.LEXICAL,
            page_size=page_size,
            offset=offset or 0,
            sort=SearchSort.parse(sort),
            visible_statuses=PUBLIC_SEARCH_STATUSES,
            group_by_build=group_by_build,
        )
    )
    builds = await hydrate_builds(build_queries, result.hits)
    items: list[SearchResult] = []
    for hit in result.hits:
        match hit.resource_kind:
            case "build":
                build = builds.get(build_hit_id(hit.source_id))
                # Dropped rather than rendered from the projection: a hit without an authoritative
                # row means the index is stale, and hydrate_builds has already raised the alarm.
                if build is not None:
                    items.append(
                        BuildSearchResult(
                            score=hit.score,
                            build=build,
                            grouped=hit.grouped,
                            records=[RecordSearchEntry.from_domain(record) for record in hit.records],
                            matches=list(hit.matches),
                            title_match=hit.title_match
                            if hit.title_match is not None and hit.title_match.text == build.title
                            else None,
                        )
                    )
            case "record":
                items.append(RecordSearchResult(score=hit.score, record=RecordSearchEntry.from_domain(hit)))
            case "metadata":
                items.append(
                    MetadataSearchResult(
                        score=hit.score,
                        metadata=MetadataSearchEntry.from_domain(hit),
                        matches=list(hit.matches),
                        title_match=hit.title_match,
                    )
                )
    return Page(
        items=items,
        total=result.total,
        next=PageAnchor.from_domain(result.next) if result.next is not None else None,
        prev=PageAnchor.from_domain(result.prev) if result.prev is not None else None,
    )


def build_hit_id(source_id: str) -> int:
    """Parse the build ID stored in a search hit.

    An invalid ID raises DataIntegrityError because it indicates a broken search
    index, not a query the caller can correct.
    """
    try:
        return int(source_id)
    except ValueError as error:
        msg = "A search projection is keyed by an unparsable build identifier."
        raise DataIntegrityError(msg, context={"source_id": source_id}) from error


async def hydrate_builds(build_queries: BuildQueryService, hits: Sequence[SearchHit]) -> dict[int, BuildSummary]:
    """Load current build rows for search hits and log any missing rows.

    Build descriptions and other facts may have changed since indexing. Render
    build results from these rows rather than copying the search projection.
    """
    hit_ids = [build_hit_id(hit.source_id) for hit in hits if hit.resource_kind == "build"]
    if not hit_ids:
        return {}
    builds = await build_queries.get_many(hit_ids)
    summaries = {build.id: BuildSummary.from_domain(build) for build in builds if build.id is not None}
    missing = [identifier for identifier in hit_ids if identifier not in summaries]
    if missing:
        logger.warning("Search projection referenced missing builds", extra={"build_ids": missing})
    return summaries


router = Router(
    dependencies={
        "build_queries": Provide(get_build_queries, sync_to_thread=False),
        "search_service": Provide(get_search, sync_to_thread=False),
    },
    path="/search",
    tags=["search"],
    route_handlers=[list_search_fields, suggest_terms, search],
)
