"""Cross-resource search and suggestion route tests."""

from types import SimpleNamespace
from typing import NamedTuple, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.v1.schemas.search import BuildSearchResult, MetadataSearchResult, RecordSearchResult
from squid.api.v1.search import build_hit_id, search, suggest_terms
from squid.builds.domain import Build, DoorBuild, Status
from squid.core.errors import DataIntegrityError
from squid.core.pagination import PageAnchor
from squid.runtime import ApiServices
from squid.search.domain import (
    BuildSearchHit,
    MetadataSearchHit,
    RecordSearchHit,
    SearchPage,
    SearchScope,
)


class Fakes(NamedTuple):
    """A search-only service graph plus the mocks its routes are expected to drive."""

    services: ApiServices
    search: AsyncMock
    suggest: AsyncMock


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
    search_mock = AsyncMock(return_value=page)
    suggest_mock = AsyncMock(return_value=("piston",))
    services = SimpleNamespace(
        search=SimpleNamespace(search=search_mock, suggest=suggest_mock),
        build_queries=SimpleNamespace(get_many=AsyncMock(return_value=builds or [])),
    )
    return Fakes(cast(ApiServices, services), search_mock, suggest_mock)


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
                record_class="smallest",
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

    result = await search(graph.services.search, graph.services.build_queries, q="piston", scope=SearchScope.ALL)

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


@pytest.mark.asyncio
async def test_search_never_widens_build_visibility_past_confirmed() -> None:
    graph = fakes(empty_page())

    await search(
        graph.services.search,
        graph.services.build_queries,
        q="status:pending",
        scope=SearchScope.ALL,
    )

    assert graph.search.await_args is not None
    request = graph.search.await_args.args[0]
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

    result = await search(
        graph.services.search,
        graph.services.build_queries,
        q="piston",
        scope=SearchScope.BUILDS,
    )

    assert result.items == []


@pytest.mark.asyncio
async def test_suggest_passes_the_caller_limit_through() -> None:
    graph = fakes(empty_page())

    result = await suggest_terms(graph.services.search, q="pist", limit=3)

    assert result.suggestions == ["piston"]
    graph.suggest.assert_awaited_once_with("pist", limit=3)


def test_an_unparsable_projection_key_is_a_server_fault_not_a_bad_request() -> None:
    """A build projection keyed by something that is not an id means the index is
    lying about itself. Nothing the caller can send fixes that, so blaming them
    with a 400 sent them chasing their own query."""
    with pytest.raises(DataIntegrityError) as raised:
        build_hit_id("b1")

    assert raised.value.context == {"source_id": "b1"}
