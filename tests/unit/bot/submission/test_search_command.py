"""How `/search` turns its typed options into a search request."""

from dataclasses import dataclass
from typing import Any, cast

import pytest
from discord.ext import commands

import squid_ui_discord as sd

from squid.bot.submission.search import SearchCog, SearchTarget
from squid.builds.application import BuildQueryService
from squid.builds.domain import Build
from squid.core.errors import ValidationError
from squid.search.application import SearchService
from squid.search.domain import SearchMode, SearchPage, SearchRequest, SearchScope, SortDirection
from squid.settings.application import SettingsService
from squid_ui_discord.testing import ContextHarness, MessageHarness
from tests.support.discord import make_layout_bot


class RecordingSearch(SearchService):
    """A search service that keeps the request it was handed."""

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchPage:
        self.requests.append(request)
        return SearchPage(hits=(), total=0, next=None, prev=None)


class BuildQueryRecorder(BuildQueryService):
    def __init__(self) -> None:
        pass

    async def get(self, build_id: int) -> Build | None:
        return None


class SettingsRecorder(SettingsService):
    def __init__(self) -> None:
        pass

    async def get_locale(self, server_id: int) -> str | None:
        return None


@dataclass(frozen=True)
class CogServices:
    settings: SettingsService


@dataclass(frozen=True)
class CogBot:
    services: CogServices


def _cog(search: RecordingSearch) -> SearchCog[Any]:
    cog = SearchCog.__new__(SearchCog)
    cog.bot = cast(Any, CogBot(services=CogServices(settings=SettingsRecorder())))
    cog.queries = BuildQueryRecorder()
    cog.search = search
    return cog


def _context() -> commands.Context[Any]:
    context = ContextHarness(message=MessageHarness(message_id=1), bot=make_layout_bot(), user_id=7)
    context.guild = None
    return cast(commands.Context[Any], context.source)


async def _run(cog: SearchCog[Any], **kwargs: Any) -> None:
    context = _context()
    cog.ui = sd.DiscordUIRuntime.of(context).scope(cog)
    await cog._show_search(context, **kwargs)


async def test_a_descending_sort_suggestion_survives_the_trip() -> None:
    """The `search_sorts` source suggests `-width`, so the command has to accept it.

    Reading the option as a bare field name and taking the direction from a second
    option left the minus sign inside the field name, and the backend then rejected
    `-width` as unsortable — every descending value the autocomplete offered failed.
    """
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
    """`/patterns search` went away because this option does its job."""
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
