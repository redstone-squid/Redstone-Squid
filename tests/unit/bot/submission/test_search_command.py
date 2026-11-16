"""How `/search` turns its typed options into a search request."""

from dataclasses import dataclass, replace
from inspect import unwrap
from types import SimpleNamespace
from typing import Any, cast, override
from unittest.mock import AsyncMock, patch

import pytest

import squid_ui as sl
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.catalogue.search.entry import SearchTarget
from squid.bot.catalogue.search.results import SearchScreen, _highlighted, _result_description, _result_label
from squid.bot.submission.cog import BuildCommands, SearchModeChoice
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.records.application import RecordService
from squid.runtime import BotServices
from squid.search.application import SearchService
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.search.application.parser import SearchQueryParser
from squid.search.domain import (
    BuildSearchHit,
    RecordSearchHit,
    SearchMode,
    SearchPage,
    SearchRequest,
    SearchScope,
    SearchText,
    SortDirection,
)
from squid.search.domain.fields import FieldDefinition, FieldType
from squid.settings.application import SettingsService
from squid_i18n import source_text
from squid_ui.ext.discord.testing import stage
from tests.support.services import bot_services
from tests.unit.bot.catalogue.test_build_browse_state import _screen, submit_search


class RecordingSearch(SearchService):
    """A search service that keeps the request it was handed."""

    def __init__(self, registry: FieldRegistry = DEFAULT_FIELD_REGISTRY) -> None:
        self.requests: list[SearchRequest] = []
        self.registry = registry

    @override
    async def fields(self) -> FieldRegistry:
        return self.registry

    @override
    async def search(self, request: SearchRequest) -> SearchPage:
        self.requests.append(request)
        return SearchPage(hits=(), total=0, next=None, prev=None)


class BuildQueryRecorder(BuildQueryService):
    def __init__(self) -> None:
        pass

    @override
    async def get(self, build_id: int) -> Build | None:
        return None


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    @override
    async def get_locale(self, server_id: int) -> str | None:
        return None


@dataclass(frozen=True)
class CogBot:
    services: BotServices


def _cog(search: RecordingSearch) -> BuildCommands[Any]:
    cog = BuildCommands.__new__(BuildCommands)
    cog.bot = cast(
        Any,
        CogBot(services=bot_services(settings=SettingsRecorder(), build_queries=BuildQueryRecorder(), search=search)),
    )
    return cog


async def _run(cog: BuildCommands[Any], **kwargs: Any) -> BuildBrowseScreen | SearchScreen:
    async def create(request: Any, *, initial_records: bool = False) -> BuildBrowseScreen:
        screen = _screen(initial_id=None, records=AsyncMock(spec=RecordService), initial_records=initial_records)
        screen._search_service = cog.bot.services.search
        screen._search_parser = SearchQueryParser(await cog.bot.services.search.fields())
        return screen

    with patch.object(BuildBrowseScreen, "create", side_effect=create):
        request = cast(Any, SimpleNamespace(client=cog.bot))
        result = await unwrap(cast(Any, BuildCommands).search_records.callback)(cog, request, **kwargs)
    target = kwargs.get("scope", SearchTarget.builds)
    assert isinstance(
        result, BuildBrowseScreen if target in (SearchTarget.builds, SearchTarget.records) else SearchScreen
    )
    return result


async def test_a_descending_sort_suggestion_survives_the_trip() -> None:
    """Autocomplete emits signed sort fields; parse -width into a field and descending direction."""
    search = RecordingSearch()

    await _run(_cog(search), query="doors", sort="-width")

    sort = search.requests[0].sort
    assert sort is not None
    assert (sort.field, sort.direction) == ("width", SortDirection.DESCENDING)


async def test_an_ascending_sort_needs_no_punctuation() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="doors", sort="width")

    sort = search.requests[0].sort
    assert sort is not None
    assert (sort.field, sort.direction) == ("width", SortDirection.ASCENDING)


async def test_no_sort_option_leaves_relevance_ordering_alone() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="doors")

    assert search.requests[0].scope is SearchScope.BUILDS
    assert search.requests[0].sort is None
    assert search.requests[0].mode is SearchMode.LEXICAL
    assert search.requests[0].group_by_build


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("smallest 5x5", "smallest"),
        ("fastest 5x5", "fastest"),
        ("smallest fastest 5x5", "smallest_fastest"),
        ("FASTEST smallest 5x5", "fastest_smallest"),
        ("base_title:smallest 5x5", "smallest"),
    ],
)
async def test_record_title_search_defaults_to_the_records_tab(query: str, title: str) -> None:
    search = RecordingSearch()

    screen = await _run(_cog(search), query=query)

    assert isinstance(screen, BuildBrowseScreen)
    assert screen._mode == "records"
    assert search.requests[0].scope is SearchScope.RECORDS
    assert search.requests[0].query == f"base_title:{title} AND 5x5"
    async with stage(screen, owner=object()) as ui:
        ui.assert_within_limits()


@pytest.mark.parametrize("query", ['"smallest" 5x5', "5x5 smallest", "title:smallest 5x5"])
async def test_literal_record_words_keep_the_builds_tab(query: str) -> None:
    search = RecordingSearch()

    screen = await _run(_cog(search), query=query)

    assert isinstance(screen, BuildBrowseScreen)
    assert screen._mode == "builds"
    assert search.requests[0].scope is SearchScope.BUILDS


async def test_explicit_build_scope_overrides_the_record_title_filter() -> None:
    search = RecordingSearch()

    screen = await _run(_cog(search), query="smallest 5x5", scope=SearchTarget.builds)

    assert isinstance(screen, BuildBrowseScreen)
    assert screen._mode == "builds"
    assert search.requests[0].scope is SearchScope.BUILDS


async def test_explicit_record_scope_expands_shorthand_without_status_filter() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="smallest 5x5", scope=SearchTarget.records)

    assert search.requests[0].query == "base_title:smallest AND 5x5"


async def test_records_remain_an_explicit_search_target() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="width:5", scope=SearchTarget.records)

    assert search.requests[0].scope is SearchScope.RECORDS
    assert search.requests[0].query == "width:5"
    assert search.requests[0].group_by_build


async def test_a_sort_of_only_a_minus_sign_is_a_user_error() -> None:
    """`SearchSort.parse` rejects it as invalid input rather than inventing a field."""
    with pytest.raises(ValidationError):
        await _run(_cog(RecordingSearch()), query="doors", sort="-")


async def test_a_taxonomy_target_narrows_the_query_for_you() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="lacing", scope=SearchTarget.patterns)

    request = search.requests[0]
    assert request.scope is SearchScope.METADATA
    assert request.query == "kind:pattern (lacing)"


async def test_a_taxonomy_target_alone_lists_that_taxonomy() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="  ", scope=SearchTarget.restrictions)

    assert search.requests[0].query == "kind:restriction"


async def test_the_users_text_is_parenthesised_so_or_cannot_escape() -> None:
    """`kind:pattern a OR b` would parse as `(kind:pattern AND a) OR b`."""
    search = RecordingSearch()

    await _run(_cog(search), query="a OR b", scope=SearchTarget.patterns)

    assert search.requests[0].query == "kind:pattern (a OR b)"


async def test_a_plain_target_leaves_the_query_untouched() -> None:
    search = RecordingSearch()

    await _run(_cog(search), query="a OR b", scope=SearchTarget.everything)

    assert search.requests[0].scope is SearchScope.ALL
    assert search.requests[0].query == "a OR b"


@pytest.mark.parametrize(
    "query",
    [
        "status:unresolved",
        "status:resolved OR status:no_candidate",
        "NOT status:resolved",
        "NOT (status:resolved OR kind:door)",
    ],
)
async def test_explicit_record_status_is_preserved(query: str) -> None:
    search = RecordingSearch()
    screen = await _run(_cog(search), query=query, scope=SearchTarget.records)
    assert isinstance(screen, BuildBrowseScreen)
    assert screen._query == query
    assert search.requests[0].query == query


@pytest.mark.parametrize("query", ["", "   ", "door OR kind:extender"])
async def test_record_search_does_not_add_a_status_filter(query: str) -> None:
    search = RecordingSearch()
    screen = await _run(_cog(search), query=query, scope=SearchTarget.records)
    assert isinstance(screen, BuildBrowseScreen)
    normalized = SearchQueryParser().parse(query).normalized
    expected = normalized
    assert screen._query == search.requests[0].query == expected


@pytest.mark.parametrize("scope", [SearchTarget.builds, SearchTarget.records])
async def test_smart_search_and_sort_survive_editing_the_catalogue_query(scope: SearchTarget) -> None:
    search = RecordingSearch()
    screen = await _run(_cog(search), query="door", scope=scope, mode=SearchModeChoice.smart, sort="-width")
    assert isinstance(screen, BuildBrowseScreen)
    assert search.requests[0].mode is SearchMode.SEMANTIC
    assert screen._results is not None
    assert screen._results._build_detail is not None
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "width:5", "-height")
        assert search.requests[-1].mode is SearchMode.SEMANTIC
        assert search.requests[-1].sort is not None
        assert search.requests[-1].sort.field == "height"
        assert search.requests[-1].sort.direction is SortDirection.DESCENDING
        assert search.requests[-1].scope is (
            SearchScope.RECORDS if scope is SearchTarget.records else SearchScope.BUILDS
        )
        ui.assert_within_limits()


@pytest.mark.parametrize("scope", [SearchTarget.builds, SearchTarget.records])
async def test_registered_fields_survive_opening_the_catalogue(scope: SearchTarget) -> None:
    registry = FieldRegistry((*DEFAULT_FIELD_REGISTRY.definitions, FieldDefinition("custom_flag", FieldType.BOOLEAN)))
    search = RecordingSearch(registry)
    screen = await _run(_cog(search), query="custom_flag:true", scope=scope)
    assert isinstance(screen, BuildBrowseScreen)
    async with stage(screen, owner=object()) as ui:
        assert "custom_flag:true" in screen._query
        assert search.requests[0].query == screen._query
        await ui.press(ui.key_for("guided-search"))
        ui.control(ui.key_for("edit-0"))
        ui.assert_within_limits()


async def test_search_jump_form_preserves_the_query_and_fetches_the_requested_page() -> None:
    class PagedSearch(RecordingSearch):
        @override
        async def search(self, request: SearchRequest) -> SearchPage:
            self.requests.append(request)
            return SearchPage(
                hits=tuple(
                    BuildSearchHit(str(index), f"Door {index}", "confirmed")
                    for index in range(request.offset, request.offset + request.page_size)
                ),
                total=200,
                next=PageAnchor(offset=request.offset + request.page_size),
                prev=PageAnchor(offset=request.offset - request.page_size) if request.offset else None,
            )

    service = PagedSearch()
    request = SearchRequest("door width:2", page_size=5)
    screen = SearchScreen(service, request, await service.search(request))
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("search.seek.jump"))
        await form.submit({"page": "37"})
        assert service.requests[-1].offset == 180
        assert service.requests[-1].query == request.query
        assert screen.request.offset == 180
        assert any("Door 180" in text for text in ui.texts())
        ui.assert_within_limits()


@pytest.mark.parametrize("count", [2, 6])
async def test_long_search_titles_fit_picker_controls_and_timeout(count: int) -> None:
    # Small result sets become buttons (80 characters); larger sets become selects (100).
    title = "A very elaborate redstone door " * 5
    page = SearchPage(
        hits=tuple(BuildSearchHit(str(index), title, "confirmed") for index in range(count)),
        total=count,
        next=None,
        prev=None,
    )
    screen = SearchScreen(RecordingSearch(), SearchRequest("door"), page)
    async with stage(screen, owner=object()) as ui:
        assert any(title in text for text in ui.texts())
        ui.assert_within_limits()
        await ui.root.handle_timeout()
        ui.assert_within_limits()


def grouped_build() -> BuildSearchHit:
    return BuildSearchHit(
        "42",
        "2x2 Seamless Door",
        "confirmed",
        title_match=SearchText("2x2 Seamless Door", ((0, 3),)),
        records=(
            RecordSearchHit(
                "result:1",
                "Smallest 2x2 Door",
                "Java Edition",
                42,
                "2x2 Seamless Door",
                "smallest",
                "all_time",
                holder_build_ids=(42, 57),
                metrics={"volume": 48},
                title_match=SearchText("Smallest 2x2 Door", ((9, 12),)),
            ),
            RecordSearchHit(
                "result:2",
                "Fastest 2x2 Door",
                "Java Edition",
                42,
                "2x2 Seamless Door",
                "fastest",
                "all_time",
                holder_build_ids=(42,),
                metrics={"opening": 4},
            ),
        ),
    )


def test_grouped_result_titles_follow_the_scope_and_do_not_repeat_the_heading() -> None:
    hit = grouped_build()

    def grouped(scope: SearchScope = SearchScope.RECORDS) -> SearchRequest:
        return SearchRequest("2x2", scope=scope, group_by_build=True)

    records = source_text(_result_description(hit, grouped()), format="plain")
    # Results use the catalogue's rows: a record search leads with the record, then its build.
    assert records.splitlines() == [
        "Smallest 2x2 Door · Java Edition — all time",
        "#42 2x2 Seamless Door",
        "Fastest 2x2 Door · Java Edition — all time",
    ]
    for scope in (SearchScope.BUILDS, SearchScope.ALL):
        description = source_text(_result_description(hit, grouped(scope)), format="plain")
        assert description.splitlines() == [
            "#42 2x2 Seamless Door",
            "Smallest 2x2 Door · Java Edition — all time",
            "Fastest 2x2 Door · Java Edition — all time",
        ]
    same = replace(hit, title="Smallest 2x2 Door", title_match=None, records=hit.records[:1])
    for scope in (SearchScope.BUILDS, SearchScope.ALL):
        assert source_text(_result_description(same, grouped(scope)), format="plain").splitlines() == [
            "#42 Smallest 2x2 Door",
        ]
    assert source_text(_result_description(same, grouped()), format="plain").splitlines() == [
        "Smallest 2x2 Door · Java Edition — all time",
        "#42 Smallest 2x2 Door",
    ]
    # Only matched words are bold, so highlighting stands out from the row.
    assert (
        source_text(
            _result_description(replace(hit, dimensions=(3, 4, None)), grouped(SearchScope.BUILDS)), format="markdown"
        ).splitlines()[0]
        == "#42 **2x2** Seamless Door — 3 x 4 x ?"
    )
    assert "**2x2**" in source_text(_result_description(hit, grouped()), format="markdown")
    assert _result_label(hit, grouped()) == "Smallest 2x2 Door · Java Edition · #42"
    assert _result_label(replace(hit, source_id="57"), grouped()) == "Smallest 2x2 Door · Java Edition · #57"


def test_highlights_do_not_interpret_build_titles_as_markdown() -> None:
    highlighted = _highlighted(SearchText("**door**", ((2, 6),)))
    assert source_text(highlighted, format="plain") == "**door**"
    assert source_text(highlighted, format="markdown") == r"\*\***door**\*\*"


async def test_grouped_record_and_holder_navigation_preserves_the_search_page() -> None:
    hit = grouped_build()
    page = SearchPage((hit,), total=11, next=None, prev=PageAnchor(offset=5))
    loaded: list[int] = []

    async def load(build_id: int) -> Build:
        loaded.append(build_id)
        return DoorBuild(id=build_id, submission_status=Status.CONFIRMED)

    async def render(build: Build) -> sl.LayoutNode:
        return sl.paragraph(f"Build card #{build.id}")

    screen = SearchScreen(
        RecordingSearch(),
        SearchRequest("2x2", offset=10, group_by_build=True),
        page,
        load_build=load,
        render_build=render,
    )
    async with stage(screen, owner=object()) as ui:
        await ui.select(ui.key_for("search.open"), ["build:42"])
        assert "Build card #42" in ui.texts()
        await ui.press(ui.key_for("search-show-records"))
        await ui.select(ui.key_for("search-records.open"), ["result:1"])
        await ui.press(ui.key_for("search-holder-57"))
        assert "Build card #57" in ui.texts()
        await ui.press(ui.key_for("search-back-records"))
        assert any("Smallest 2x2 Door" in text for text in ui.texts())
        await ui.press(ui.key_for("search.back"))
        assert screen.request.offset == 10
        assert screen.hits == (hit,)
        assert loaded == [42, 57]
        ui.assert_within_limits()


async def test_many_grouped_records_and_ties_remain_browsable_within_discord_limits() -> None:
    hit = grouped_build()
    hit = replace(
        hit,
        records=tuple(
            replace(
                hit.records[0],
                source_id=f"result:{index}",
                title=f"Record {index}: " + "2x2 seamless piston door " * 5,
                title_match=None,
                holder_build_ids=(42, *range(100, 130)),
            )
            for index in range(12)
        ),
    )
    screen = SearchScreen(
        RecordingSearch(),
        SearchRequest("door", group_by_build=True),
        SearchPage((hit,), total=1, next=None, prev=None),
    )
    async with stage(screen, owner=object()) as ui:
        assert any("+9 more records" in text for text in ui.texts())
        await ui.select(ui.key_for("search.open"), ["build:42"])
        await ui.press(ui.key_for("search-show-records"))
        await ui.select(ui.key_for("search-records.open"), ["result:0"])
        assert any("Tied holders" in text for text in ui.texts())
        ui.assert_within_limits()
