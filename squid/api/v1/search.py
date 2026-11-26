"""Search discovery and cross-resource matching routes."""

import logging
from collections.abc import Sequence
from typing import Annotated

from litestar import Router, get
from litestar.di import Provide
from litestar.params import QueryParameter

from squid.api.contract import ANONYMOUS, contract, transport_only
from squid.api.dependencies import BuildQueries, CurrentCaller, Search, get_build_queries, get_search
from squid.api.errors import responses
from squid.api.pagination import OffsetParam, PageAnchor, PageSizeParam
from squid.api.v1.schemas.search import (
    BuildSearchResult,
    MetadataSearchEntry,
    MetadataSearchResult,
    RecordSearchEntry,
    RecordSearchResult,
    SearchCounts,
    SearchField,
    SearchFilter,
    SearchResult,
    SearchResultPage,
    SearchSuggestions,
)
from squid.core.actors import ActingContext
from squid.core.builds.application import BuildQueryService, PublicBuildSummary
from squid.core.builds.domain import BuildStatus
from squid.core.errors import DataIntegrityError
from squid.core.search.domain import SearchHit, SearchMode, SearchRequest, SearchScope, SearchSort

logger = logging.getLogger(__name__)

# Public search always restricts results to approved builds. This separate filter
# also applies when the query itself asks for another status.
PUBLIC_SEARCH_STATUSES = frozenset({"approved"})


@get(
    "/fields",
    responses=responses(503),
    operation_id="search_fields_list",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def list_search_fields(search_service: Search) -> list[SearchField]:
    """List the fields queries can use."""
    registry = await search_service.fields(context=ActingContext.current())
    return [SearchField.from_domain(field) for field in registry.definitions]


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
    return SearchSuggestions(
        suggestions=list(await search_service.suggest(q, limit=limit, context=ActingContext.current()))
    )


@get(
    "/",
    responses=responses(400, 422, 503),
    operation_id="search_execute",
    security=[ANONYMOUS],
    opt=contract(cli=transport_only()),
)
async def search(
    caller: CurrentCaller,
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
    counts: Annotated[
        bool, QueryParameter(description="Also count the matches in every scope, for scope tabs.")
    ] = False,
) -> SearchResultPage:
    """Match builds, computed records, and tag entries in one ranked page."""
    request = SearchRequest(
        query=q,
        scope=search_scope,
        mode=SearchMode.LEXICAL,
        page_size=page_size,
        offset=offset or 0,
        sort=SearchSort.parse(sort),
        visible_statuses=PUBLIC_SEARCH_STATUSES,
        group_by_build=group_by_build,
    )
    result = await search_service.search(request, context=caller.context())
    builds = await hydrate_builds(build_queries, result.hits, context=caller.context())
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
                            grouped=group_by_build,
                            records=[RecordSearchEntry.from_domain(record) for record in hit.records],
                            matches=list(hit.matches),
                            evidence=list(hit.evidence.matches),
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
                        evidence=list(hit.evidence.matches),
                    )
                )
    scope_counts = await search_service.counts(request, context=caller.context()) if counts else None
    return SearchResultPage(
        items=items,
        total=result.total,
        next=PageAnchor.from_domain(result.next) if result.next is not None else None,
        prev=PageAnchor.from_domain(result.prev) if result.prev is not None else None,
        filters=[SearchFilter.from_domain(clause) for clause in result.clauses],
        counts=SearchCounts.from_domain(scope_counts) if scope_counts is not None else None,
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


async def hydrate_builds(
    build_queries: BuildQueryService,
    hits: Sequence[SearchHit],
    *,
    context: ActingContext,
) -> dict[int, PublicBuildSummary]:
    """Load current build rows for search hits and log any missing rows.

    Build descriptions and other facts may have changed since indexing. Render
    build results from these rows rather than copying the search projection.
    """
    hit_ids = [build_hit_id(hit.source_id) for hit in hits if hit.resource_kind == "build"]
    if not hit_ids:
        return {}
    builds = await build_queries.get_many(hit_ids, context=context)
    # ORM identity columns may still be None before flush.
    summaries = {
        build.id: PublicBuildSummary.from_build(build)
        for build in builds
        if build.submission_status is BuildStatus.APPROVED and build.id is not None  # pyright: ignore[reportUnnecessaryComparison]
    }
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
