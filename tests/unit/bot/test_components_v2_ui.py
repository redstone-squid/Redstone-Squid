"""Structural tests for the bot's semantic Components V2 workflows."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest

import squid_ui_discord as sd
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.ui.views import EDIT_FIELDS, BuildEditScreen
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildLink, DoorBuild, SourceMessage, Status
from squid.search.application import SearchService
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchPage, SearchRequest
from squid.sponsors import PublicSponsor
from squid.versions.application import VersionService
from squid.versions.domain import Edition
from squid_ui_discord.testing import commit_render, delivered_to, message_harness
from tests.support.discord import make_layout_bot

if TYPE_CHECKING:
    import squid.bot.app


class VersionRecorder(VersionService):
    def __init__(self) -> None:
        pass

    async def newest(self, edition: Edition) -> str:
        assert edition == "Java"
        return "Java 1.20"


class SearchRecorder(SearchService):
    def __init__(self) -> None:
        self.calls: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchPage:
        self.calls.append(request)
        raise AssertionError("the seeded first page must not be fetched again")


class BuildRecorder(BuildService):
    def __init__(self) -> None:
        pass


@dataclass(frozen=True)
class HandlerServices:
    versions: VersionService


@dataclass(frozen=True)
class HandlerBot:
    services: HandlerServices


def handler_bot() -> Any:
    return HandlerBot(HandlerServices(VersionRecorder()))


@pytest.fixture
def display_build() -> Build:
    return DoorBuild(
        id=7,
        submission_status=Status.PENDING,
        width=5,
        height=6,
        depth=7,
        door_width=2,
        door_height=2,
        door_depth=1,
        patterns=["Regular"],
        orientation="Door",
        component_restrictions=["Observerless"],
        creators_ign=["Builder"],
        version_spec="1.20+",
        versions=["Java 1.20"],
        links=[BuildLink(url="https://example.com/build.png", media_type="image")],
        extra_info={},
        source_messages=(SourceMessage(message_id=3, guild_id=1, channel_id=2),),
    )


@pytest.mark.asyncio
async def test_build_handler_renders_composable_v2_card(display_build: Build) -> None:
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", handler_bot()), display_build)

    presentation = await handler.render_payload()
    payload = presentation.layout.to_components()

    assert presentation.layout.has_components_v2()
    assert payload[0]["type"] == discord.ComponentType.container.value
    assert "Pending: Observerless 2x2 Door" in str(payload)
    assert "https://example.com/build.png" in str(payload)
    assert "https://discord.com/channels/1/2/3" in str(payload)
    assert "Submission ID: 7" in str(payload)


async def test_build_editor_uses_semantic_state_and_forms(display_build: Build) -> None:
    field = next(spec for spec in EDIT_FIELDS if spec.patch_key == "version_spec").bind(display_build)
    component = BuildEditScreen(
        display_build,
        BuildRecorder(),
        [field],
        authorize=AsyncMock(return_value=True),
        render_build=AsyncMock(),
        refresh_posts=AsyncMock(),
    )

    # `projection` is an atomic resource, so reading its status aborts a discovery render
    # until it has settled. A mount settles it before rendering; calling `render()` straight
    # from a test has to do the same. The loader returns the seed the constructor supplied,
    # so this touches no service.
    await component.projection.reload()

    assert component.max_pages == 1
    assert "Edit this section" in str(component.render())
    assert component._current()[0] is display_build


def test_build_editor_declares_keyed_topic_following_policy() -> None:
    assert BuildEditScreen.session is not None
    assert BuildEditScreen.session.name == "build-edit"
    assert BuildEditScreen.timeout == 900
    assert BuildEditScreen.follow_topics is True


@pytest.mark.asyncio
async def test_build_handler_does_not_repeat_headline_component_restrictions(display_build: Build) -> None:
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", handler_bot()), display_build)

    assert await handler.get_description() is None


def test_build_handler_credits_the_sponsoring_server_and_website(display_build: Build) -> None:
    build = replace(
        display_build,
        sponsor=PublicSponsor(
            UUID("00000000-0000-4000-8000-000000000801"),
            display_name="Sponsor Network",
            address="play.example.test",
            website_url="https://example.test/",
        ),
    )
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", object()), build)

    metadata = handler.get_metadata_fields()

    assert metadata["Sponsoring Server"] == "Sponsor Network"
    assert metadata["Sponsor Website"] == "https://example.test/"


def test_search_results_use_named_selection_and_direct_build_action() -> None:
    record = RecordSearchHit(
        "record-1",
        "Smallest 2x2 door",
        None,
        7,
        "Observerless 2x2 Door",
        "smallest",
        "Java 1.20+",
    )
    page = SearchPage(
        (record, BuildSearchHit("8", "Fast door", "confirmed")),
        total=1,
        next=None,
        prev=None,
    )
    view = SearchScreen(SearchRecorder(), SearchRequest("door"), page)

    bot = make_layout_bot()
    message_root = bot.ui.mount(view, access=sd.Owner(123), timeout=180)
    rendered = commit_render(message_root)
    result_buttons = [
        child
        for child in rendered.walk_children()
        if isinstance(child, discord.ui.Button) and child.label in {"Smallest 2x2 door", "Fast door"}
    ]

    assert [button.label for button in result_buttons] == ["Smallest 2x2 door", "Fast door"]
    assert "Close" in str(rendered.to_components())


def test_search_results_preserve_page_warnings_without_refetching_the_initial_page() -> None:
    service = SearchRecorder()
    page = SearchPage(
        (BuildSearchHit("8", "Fast door", "confirmed"),),
        total=1,
        next=None,
        prev=None,
        warnings=("Semantic fallback used",),
    )
    view = SearchScreen(service, SearchRequest("door"), page)

    bot = make_layout_bot()
    payload = commit_render(bot.ui.mount(view, access=sd.Owner(123), timeout=180)).to_components()

    assert service.calls == []
    assert "Semantic fallback used" in str(payload)


@pytest.mark.asyncio
async def test_search_timeout_disables_bound_controls() -> None:
    page = SearchPage((BuildSearchHit("8", "Fast door", "confirmed"),), total=1, next=None, prev=None)
    view = SearchScreen(SearchRecorder(), SearchRequest("door"), page)
    bot = make_layout_bot()
    message_root = bot.ui.mount(view, access=sd.Owner(123), timeout=180)
    message = message_harness()
    await message_root.send(delivered_to(message))

    await message_root.finish()

    message.edit.assert_awaited_once()
    disabled = message.edit.await_args.kwargs["view"]
    controls = [child for child in disabled.walk_children() if isinstance(child, discord.ui.Button | discord.ui.Select)]
    assert controls
    assert all(control.disabled for control in controls)
