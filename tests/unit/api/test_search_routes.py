"""Cross-resource search and suggestion route tests."""

from types import SimpleNamespace
from typing import NamedTuple, cast
from unittest.mock import AsyncMock

import pytest

from squid.api.v1.schemas.search import BuildSearchResult, MetadataSearchResult, RecordSearchResult
from squid.api.v1.search import search, suggest_terms
from squid.builds.domain import Build, BuildCategory, Status
from squid.runtime import ApplicationServices
from squid.search.domain import (
    BuildSearchHit,
    MetadataSearchHit,
    RecordSearchHit,
    SearchPage,
    SearchScope,
)


class Fakes(NamedTuple):
    """A search-only service graph plus the mocks its routes are expected to drive."""

    services: ApplicationServices
    search: AsyncMock
    suggest: AsyncMock


def indexed_build(build_id: int = 42) -> Build:
    return Build(
        id=build_id,
        submitter_id=123,
        category=BuildCategory.DOOR,
        submission_status=Status.CONFIRMED,
        versions=["1.21"],
        door_width=2,
        door_height=2,
        door_type=["Regular"],
        door_orientation_type="Door",
    )


def fakes(page: SearchPage, builds: list[Build] | None = None) -> Fakes:
    search_mock = AsyncMock(return_value=page)
    suggest_mock = AsyncMock(return_value=("piston",))
    services = SimpleNamespace(
        search=SimpleNamespace(search=search_mock, suggest=suggest_mock),
        build_queries=SimpleNamespace(get_many=AsyncMock(return_value=builds or [])),
    )
    return Fakes(cast(ApplicationServices, services), search_mock, suggest_mock)


def empty_page() -> SearchPage:
    return SearchPage(hits=(), next_cursor=None, has_more=False)


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
        next_cursor="next",
        has_more=True,
    )
    graph = fakes(page, [indexed_build()])

    result = await search(graph.services, q="piston", scope=SearchScope.ALL)

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
    assert result.next_cursor == "next"
    assert result.has_more is True


@pytest.mark.asyncio
async def test_search_never_widens_build_visibility_past_confirmed() -> None:
    graph = fakes(empty_page())

    await search(graph.services, q="status:pending", scope=SearchScope.ALL)

    assert graph.search.await_args is not None
    request = graph.search.await_args.args[0]
    assert request.visible_statuses == frozenset({"confirmed"})


@pytest.mark.asyncio
async def test_search_drops_hits_whose_build_has_vanished() -> None:
    page = SearchPage(
        hits=(BuildSearchHit(source_id="42", title="Gone", status="confirmed"),),
        next_cursor=None,
        has_more=False,
    )
    graph = fakes(page)

    result = await search(graph.services, q="piston", scope=SearchScope.BUILDS)

    assert result.items == []


@pytest.mark.asyncio
async def test_suggest_passes_the_caller_limit_through() -> None:
    graph = fakes(empty_page())

    result = await suggest_terms(graph.services, q="pist", limit=3)

    assert result.suggestions == ["piston"]
    graph.suggest.assert_awaited_once_with("pist", limit=3)
