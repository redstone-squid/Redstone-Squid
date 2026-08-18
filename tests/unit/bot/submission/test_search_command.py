"""How `/search` turns its typed options into a search request."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands

from squid.bot.submission.search import SearchCog
from squid.core.errors import ValidationError
from squid.search.domain import SearchMode, SearchPage, SearchRequest, SortDirection


class RecordingSearch:
    """A search service that keeps the request it was handed."""

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchPage:
        self.requests.append(request)
        return SearchPage(hits=(), total=0, next=None, prev=None)


def _cog(search: RecordingSearch) -> SearchCog[Any]:
    cog = SearchCog.__new__(SearchCog)
    cog.bot = cast(Any, SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace())))
    cog.search = cast(Any, search)
    return cog


def _context() -> commands.Context[Any]:
    return cast(
        commands.Context[Any],
        cast(
            Any,
            SimpleNamespace(
                defer=AsyncMock(),
                send=AsyncMock(return_value=SimpleNamespace(id=1)),
                guild=None,
                interaction=None,
                author=SimpleNamespace(id=7),
            ),
        ),
    )


async def _run(cog: SearchCog[Any], **kwargs: Any) -> None:
    await SearchCog.search_records.callback(cog, _context(), **kwargs)  # type: ignore[arg-type]


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
