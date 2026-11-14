"""How `/search` turns its typed options into a search request."""

from dataclasses import dataclass, replace
from typing import Any, cast, override

import pytest

import squid_ui as sl
from squid.bot.submission.commands import BuildCommands, SearchTarget
from squid.bot.submission.search_view import SearchScreen, _highlighted, _result_description, _result_label
from squid.builds.application import BuildQueryService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.runtime import BotServices
from squid.search.application import SearchService
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
from squid.settings.application import SettingsService
from squid_i18n import source_text
from squid_ui_discord.testing import stage
from tests.support.services import bot_services


class RecordingSearch(SearchService):
    """A search service that keeps the request it was handed."""

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

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
    cog.bot = cast(Any, CogBot(services=bot_services(settings=SettingsRecorder())))
    cog.queries = BuildQueryRecorder()
    cog.search = search
    return cog


async def _run(cog: BuildCommands[Any], **kwargs: Any) -> None:
    await cog._search_screen(**kwargs)


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

    assert search.requests[0].sort is None
    assert search.requests[0].mode is SearchMode.LEXICAL
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
    assert records.splitlines() == ["Smallest 2x2 Door", "Build #42 · 2x2 Seamless Door", "Fastest 2x2 Door"]
    for scope in (SearchScope.BUILDS, SearchScope.ALL):
        description = source_text(_result_description(hit, grouped(scope)), format="plain")
        assert description.splitlines() == ["2x2 Seamless Door", "Build #42", "Smallest 2x2 Door", "Fastest 2x2 Door"]
    same = replace(hit, title="Smallest 2x2 Door", title_match=None, records=hit.records[:1])
    for scope in (SearchScope.BUILDS, SearchScope.ALL, SearchScope.RECORDS):
        assert source_text(_result_description(same, grouped(scope)), format="plain").splitlines() == [
            "Smallest 2x2 Door",
            "Build #42",
        ]
    assert "**2x2**" in source_text(_result_description(hit, grouped()), format="markdown")
    assert _result_label(hit, grouped()) == "Smallest 2x2 Door · #42"
    assert _result_label(replace(hit, source_id="57"), grouped()) == "Smallest 2x2 Door · #57"


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
        await ui.press(ui.key_for("search-records.open.result:1"))
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
        await ui.press(ui.key_for("search-records.open.result:0"))
        assert any("Tied holders" in text for text in ui.texts())
        ui.assert_within_limits()
