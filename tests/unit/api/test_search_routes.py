"""Cross-resource search and suggestion route tests."""

from collections.abc import Sequence
from typing import NamedTuple, override

import pytest

from squid.api.v1.schemas.search import BuildSearchResult, MetadataSearchResult, RecordSearchResult
from squid.api.v1.search import build_hit_id, search, suggest_terms
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.errors import DataIntegrityError
from squid.core.pagination import PageAnchor
from squid.search.application import SearchService
from squid.search.domain import (
    BuildSearchHit,
    MetadataSearchHit,
    RecordSearchHit,
    SearchMatch,
    SearchPage,
    SearchQuery,
    SearchRequest,
    SearchScope,
    SearchText,
)


class Fakes(NamedTuple):
    """Search and build-query service subclasses driven by route tests."""

    search: SearchRecorder
    build_queries: BuildQueryFake


class SearchRecorder(SearchService):
    def __init__(self, page: SearchPage) -> None:
        self.page = page
        self.requests: list[SearchRequest] = []
        self.suggestions: list[tuple[str, int]] = []

    @override
    async def search(self, request: SearchRequest) -> SearchPage:
        self.requests.append(request)
        return self.page

    @override
    async def suggest(self, query: str | SearchQuery, *, limit: int = 5) -> tuple[str, ...]:
        self.suggestions.append((str(query), limit))
        return ("piston",)


class BuildQueryFake(BuildQueryService):
    def __init__(self, builds: list[Build]) -> None:
        self.builds = builds
        self.requested: list[tuple[int, ...]] = []

    @override
    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        self.requested.append(tuple(build_ids))
        return self.builds


def indexed_build(build_id: int = 42) -> Build:
    return DoorBuild(
        id=build_id,
        submitter_account_id=123,
        submission_status=Status.CONFIRMED,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        patterns=["Regular"],
        orientation="Door",
    )


def fakes(page: SearchPage, builds: list[Build] | None = None) -> Fakes:
    return Fakes(SearchRecorder(page), BuildQueryFake(builds or []))


def empty_page() -> SearchPage:
    return SearchPage(hits=(), total=0, next=None, prev=None)


@pytest.mark.asyncio
async def test_search_renders_each_resource_kind_and_hydrates_builds() -> None:
    page = SearchPage(
        hits=(
            BuildSearchHit(source_id="42", title="Stale projection title", status="confirmed", score=1.5),
            RecordSearchHit(
                source_id="result:7",
                title="Smallest 2x2",
                subtitle="all-time",
                build_id=42,
                build_title="A door",
                base_title="smallest",
                version_scope="all-time",
                score=1.0,
                metrics={"volume": 30},
            ),
            MetadataSearchHit(source_id="tag:3", title="Seamless", metadata_kind="tag", score=0.5),
        ),
        total=9,
        next=PageAnchor(offset=3),
        prev=None,
    )
    graph = fakes(page, [indexed_build()])

    result = await search.fn(graph.search, graph.build_queries, q="piston", search_scope=SearchScope.ALL)

    assert [item.resource_kind for item in result.items] == ["build", "record", "metadata"]
    build_item, record_item, metadata_item = result.items
    assert isinstance(build_item, BuildSearchResult)
    assert isinstance(record_item, RecordSearchResult)
    assert isinstance(metadata_item, MetadataSearchResult)
    # The authoritative row wins over the projection snapshot's copy of the same facts.
    assert build_item.build.title != "Stale projection title"
    assert build_item.build.id == 42
    assert record_item.record.record_id == 7
    assert metadata_item.metadata.id == "tag:3"
    assert result.total == 9
    assert result.next is not None
    assert result.next.offset == 3


async def test_grouped_api_returns_record_context_and_provenance() -> None:
    record = RecordSearchHit(
        "result:7",
        "Smallest door",
        None,
        42,
        "Seamless door",
        "smallest",
        "all_time",
        holder_build_ids=(42, 57),
        title_match=SearchText("Smallest door", ((9, 13),)),
    )
    hit = BuildSearchHit(
        "42",
        "Stale title",
        "confirmed",
        records=(record,),
        matches=(SearchMatch("record", "result:7", ("trigram",)),),
        title_match=SearchText("Stale title", ((0, 5),)),
    )
    graph = fakes(SearchPage((hit,), total=1, next=None, prev=None), [indexed_build()])

    result = await search.fn(graph.search, graph.build_queries, q="door", group_by_build=True)

    assert graph.search.requests[0].group_by_build
    item = result.items[0]
    assert isinstance(item, BuildSearchResult)
    assert item.grouped
    assert item.records[0].holder_build_ids == [42, 57]
    assert item.records[0].title_match == record.title_match
    assert item.matches == list(hit.matches)
    assert item.title_match is None  # Hydration changed the text those offsets referred to.
    assert item.model_dump(mode="json")["matches"][0]["branches"] == ["trigram"]


@pytest.mark.asyncio
async def test_search_never_widens_build_visibility_past_confirmed() -> None:
    graph = fakes(empty_page())

    await search.fn(
        graph.search,
        graph.build_queries,
        q="status:pending",
        search_scope=SearchScope.ALL,
    )

    request = graph.search.requests[0]
    assert request.visible_statuses == frozenset({"confirmed"})


@pytest.mark.asyncio
async def test_search_drops_hits_whose_build_has_vanished() -> None:
    page = SearchPage(
        hits=(BuildSearchHit(source_id="42", title="Gone", status="confirmed"),),
        total=1,
        next=None,
        prev=None,
    )
    graph = fakes(page)

    result = await search.fn(
        graph.search,
        graph.build_queries,
        q="piston",
        search_scope=SearchScope.BUILDS,
    )

    assert result.items == []


@pytest.mark.asyncio
async def test_suggest_passes_the_caller_limit_through() -> None:
    graph = fakes(empty_page())

    result = await suggest_terms.fn(graph.search, q="pist", limit=3)

    assert result.suggestions == ["piston"]
    assert graph.search.suggestions == [("pist", 3)]


def test_an_unparsable_projection_key_is_a_server_fault_not_a_bad_request() -> None:
    """Malformed IDs originate in the search index, so the error must not blame the caller's query."""
    with pytest.raises(DataIntegrityError) as raised:
        build_hit_id("b1")

    assert raised.value.context == {"source_id": "b1"}
