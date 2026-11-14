"""How `/search` turns its typed options into a search request."""

from dataclasses import dataclass
from typing import Any, cast, override

import pytest

from squid.bot.submission.commands import BuildCommands, SearchTarget
from squid.bot.submission.search_view import SearchScreen
from squid.builds.application import BuildQueryService
from squid.builds.models import Build
from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.runtime import BotServices
from squid.search.application import SearchService
from squid.search.domain import BuildSearchHit, SearchMode, SearchPage, SearchRequest, SearchScope, SortDirection
from squid.settings.application import SettingsService
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
