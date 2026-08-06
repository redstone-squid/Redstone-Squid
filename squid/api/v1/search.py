"""Search discovery and cross-resource matching routes."""

import logging
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Query

from squid.api.dependencies import Services
from squid.api.errors import responses
from squid.api.pagination import Page
from squid.api.v1.schemas.builds import BuildSummary
from squid.api.v1.schemas.search import (
    BuildSearchResult,
    MetadataSearchEntry,
    MetadataSearchResult,
    RecordSearchEntry,
    RecordSearchResult,
    SearchField,
    SearchResult,
    SearchSuggestions,
)
from squid.core.errors import ErrorCode, ValidationError
from squid.runtime import ApplicationServices
from squid.search.domain import SearchHit, SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])

# Anonymous callers see only confirmed builds. This is a transport-set SearchRequest field rather
# than a query rewrite, so `?q=status:pending` is ANDed to the empty set instead of bypassing it.
PUBLIC_SEARCH_STATUSES = frozenset({"confirmed"})


@router.get("/fields", response_model=list[SearchField], responses=responses(503))
async def list_search_fields(services: Services) -> list[SearchField]:
    """Publish the effective allowlisted query-field registry."""
    registry = await services.search.fields()
    return [SearchField.from_domain(field) for field in registry.definitions]


@router.get("/suggest", response_model=SearchSuggestions, responses=responses(400, 422, 503))
async def suggest_terms(
    services: Services,
    q: Annotated[str, Query(max_length=1_000)],
    limit: Annotated[int, Query(ge=1, le=25)] = 5,
) -> SearchSuggestions:
    """Suggest indexed terms completing a valid query."""
    return SearchSuggestions(suggestions=list(await services.search.suggest(q, limit=limit)))


@router.get("", response_model=Page[SearchResult], responses=responses(400, 422, 503))
async def search(
    services: Services,
    q: Annotated[str, Query(max_length=1_000)],
    scope: SearchScope = SearchScope.ALL,
    sort: Annotated[str | None, Query(max_length=80)] = None,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=4_096)] = None,
) -> Page[SearchResult]:
    """Match builds, computed records, and taxonomy entries in one ranked page."""
    result = await services.search.search(
        SearchRequest(
            query=q,
            scope=scope,
            mode=SearchMode.LEXICAL,
            page_size=page_size,
            cursor=cursor,
            sort=parse_sort(sort),
            visible_statuses=PUBLIC_SEARCH_STATUSES,
        )
    )
    builds = await hydrate_builds(services, result.hits)
    items: list[SearchResult] = []
    for hit in result.hits:
        match hit.resource_kind:
            case "build":
                build = builds.get(build_hit_id(hit.source_id))
                # Dropped rather than rendered from the projection: a hit without an authoritative
                # row means the index is stale, and hydrate_builds has already raised the alarm.
                if build is not None:
                    items.append(BuildSearchResult(score=hit.score, build=build))
            case "record":
                items.append(RecordSearchResult(score=hit.score, record=RecordSearchEntry.from_domain(hit)))
            case "metadata":
                items.append(MetadataSearchResult(score=hit.score, metadata=MetadataSearchEntry.from_domain(hit)))
    return Page(items=items, next_cursor=result.next_cursor, has_more=result.has_more)


def parse_sort(value: str | None) -> SearchSort | None:
    """Parse a `-field` or `field` sort parameter into a domain sort."""
    if value is None:
        return None
    direction = SortDirection.DESCENDING if value.startswith("-") else SortDirection.ASCENDING
    field = value.removeprefix("-")
    if not field:
        msg = "sort field is required"
        raise ValidationError(msg, code=ErrorCode.INVALID_QUERY)
    return SearchSort(field, direction)


def build_hit_id(source_id: str) -> int:
    """Parse the build identifier a build projection is keyed by."""
    try:
        return int(source_id)
    except ValueError as error:
        msg = "search returned an invalid build identifier"
        raise ValidationError(msg) from error


async def hydrate_builds(services: ApplicationServices, hits: Sequence[SearchHit]) -> dict[int, BuildSummary]:
    """Load authoritative builds for build hits, logging any the projection outlived.

    Build projections carry a description that falls back to submitter free text and drift from
    the canonical build row, so build matches are always rendered from `BuildSummary` instead.
    """
    hit_ids = [build_hit_id(hit.source_id) for hit in hits if hit.resource_kind == "build"]
    if not hit_ids:
        return {}
    builds = await services.build_queries.get_many(hit_ids)
    summaries = {build.id: BuildSummary.from_domain(build) for build in builds if build.id is not None}
    missing = [identifier for identifier in hit_ids if identifier not in summaries]
    if missing:
        logger.warning("Search projection referenced missing builds", extra={"build_ids": missing})
    return summaries
