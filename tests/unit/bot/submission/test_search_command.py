"""How `/search` turns its typed options into a search request."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

import squid.bot.submission.search as search_module
import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.search import SearchCog, SearchTarget
from squid.builds.domain import OtherBuild
from squid.core.errors import ValidationError
from squid.search.domain import SearchMode, SearchPage, SearchRequest, SearchScope, SortDirection
from squid.topics import resource_topic
from squid_ui_discord import Everyone
from squid_ui_discord.testing import fake_interaction, fake_message
from tests.helpers.discord import make_layout_bot


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
                bot=make_layout_bot(),
                defer=AsyncMock(),
                send=AsyncMock(return_value=fake_message(message_id=1)),
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


async def test_public_build_panel_recovers_background_refresh_after_its_followup_token_stales(monkeypatch) -> None:
    interaction = fake_interaction()
    public_message = fake_message(message_id=42, ephemeral=False)
    interaction.followup.send.return_value = public_message
    build = OtherBuild(id=42)
    renderer = SimpleNamespace(render_node=AsyncMock(side_effect=[sl.paragraph("Build 42"), sl.paragraph("Build 43")]))
    topic_bus = sl.runtime.LocalTopicBus()
    layout_scheduler = sd.MessageRootScheduler(topic_bus)
    bot = SimpleNamespace(
        services=SimpleNamespace(settings=SimpleNamespace()),
        for_build=lambda current: renderer,
        topic_bus=topic_bus,
        client_runtime=SimpleNamespace(scheduler=layout_scheduler),
    )
    cog = SearchCog.__new__(SearchCog)
    cog.bot = cast(Any, bot)
    queries = SimpleNamespace(get=AsyncMock(return_value=build))
    cog.queries = cast(Any, queries)
    message_roots: list[sd.MessageRoot] = []

    def capture_root(component: sl.Component, **kwargs: Any) -> sd.MessageRoot:
        message_root = sd.MessageRoot(component, access=Everyone(), timeout=None, scheduler=kwargs.get("scheduler"))
        message_roots.append(message_root)
        return message_root

    monkeypatch.setattr(search_module, "create_message_root", capture_root)
    monkeypatch.setattr(search_module, "resolve_locale", AsyncMock(return_value=None))
    ctx = cast(
        commands.Context[Any],
        cast(Any, SimpleNamespace(interaction=interaction, author=SimpleNamespace(id=7))),
    )

    await SearchCog.view_build.callback(cog, ctx, build_id=42)  # type: ignore[arg-type]

    message_root = message_roots[0]
    # One fetch, not two: the panel's watched resource consumes the build the command already
    # loaded rather than priming itself with a second query.
    assert queries.get.await_count == 1
    assert message_root.handle is not None
    assert not message_root.handle.permanent
    interaction.followup.edit_message.side_effect = _unknown_webhook()
    cog.bot.topic_bus.publish(resource_topic("build", "42"))

    await message_root.refresh()

    assert message_root.handle is None
    assert message_root.pending

    click = fake_interaction(message_id=42)
    await message_root.dispatch("__nav_back", click)

    assert message_root.handle is not None
    assert not message_root.pending
    click.response.edit_message.assert_awaited_once()


def _unknown_webhook() -> discord.HTTPException:
    response = cast(Any, SimpleNamespace(status=404, reason="Not Found"))
    return discord.HTTPException(response, {"code": 10015, "message": "Unknown Webhook"})
