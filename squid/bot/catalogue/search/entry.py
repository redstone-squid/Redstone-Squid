"""Open the catalogue or mixed search results for a user-selected search target."""

from enum import StrEnum

from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.catalogue.record_browser import CATALOGUE_PAGE_SIZE
from squid.bot.catalogue.search.clauses import query_leaves
from squid.bot.catalogue.search.results import SearchScreen
from squid.bot.routes import AppRequest
from squid.bot.submission.build_card import render_build_card
from squid.core.actors import ActingContext
from squid.core.search.application.parser import SearchQueryParser
from squid.core.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort
from squid.core.search.domain.query import FieldExpression, NotExpression, PresenceExpression


class SearchTarget(StrEnum):
    """What a search looks through."""

    records = "records"
    builds = "builds"
    patterns = "patterns"
    restrictions = "restrictions"
    everything = "everything"


_SEARCH_TARGETS: dict[SearchTarget, tuple[SearchScope, str | None]] = {
    SearchTarget.records: (SearchScope.RECORDS, None),
    SearchTarget.builds: (SearchScope.BUILDS, None),
    SearchTarget.patterns: (SearchScope.METADATA, "kind:pattern"),
    SearchTarget.restrictions: (SearchScope.METADATA, "kind:restriction"),
    SearchTarget.everything: (SearchScope.ALL, None),
}


def _targeted(target: SearchTarget, query: str) -> tuple[SearchScope, str]:
    """Resolve a target to a domain scope and the query expressing it."""
    scope, narrowing = _SEARCH_TARGETS[target]
    if narrowing is None:
        return scope, query
    if not query.strip():
        return scope, narrowing
    return scope, f"{narrowing} ({query})"


async def open_search(
    request: AppRequest,
    *,
    scope: SearchTarget | None = None,
    sort: str | None = None,
    mode: SearchMode = SearchMode.LEXICAL,
    query: str,
) -> BuildBrowseScreen | SearchScreen:
    """Open the requested search target, inferring records from criterion when scope is omitted."""
    if scope is None:
        parsed = SearchQueryParser(await request.client.services.search.fields(context=ActingContext.current())).parse(
            query
        )
        leaves = query_leaves(parsed.expression) if parsed.expression is not None else ()
        fields = (leaf.operand if isinstance(leaf, NotExpression) else leaf for _, leaf in leaves)
        scope = (
            SearchTarget.records
            if any(
                isinstance(field, FieldExpression | PresenceExpression) and field.field.name == "criterion"
                for field in fields
            )
            else SearchTarget.builds
        )
    if scope in (SearchTarget.builds, SearchTarget.records):
        return await BuildBrowseScreen.create_search(
            request,
            query=query,
            records=scope is SearchTarget.records,
            sort=sort or "",
            mode=mode,
        )
    search_scope, targeted_query = _targeted(scope, query)
    search_request = SearchRequest(
        targeted_query,
        scope=search_scope,
        mode=mode,
        sort=SearchSort.parse(sort),
        page_size=CATALOGUE_PAGE_SIZE,
        group_by_build=True,
    )
    client = request.client
    search = client.services.search
    page = await search.search(search_request, context=ActingContext.current())
    return SearchScreen(
        search,
        search_request,
        page,
        load_build=client.services.build_queries.get,
        render_build=lambda build: render_build_card(
            build,
            client.media_previews,
            schematic_summaries=client.services.schematic_summaries,
            published_media=client.services.published_build_media,
        ),
    )
