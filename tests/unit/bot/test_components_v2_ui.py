"""Structural tests for the bot's semantic Components V2 workflows."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission import build_handler as build_handler_module
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.ui.views import EDIT_FIELDS, BuildEditScreen
from squid.bot.ui import DISCORD_GREEN, DISCORD_GREY, DISCORD_RED, DISCORD_YELLOW, DiscordColour
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildLink, DoorBuild, SourceMessage, Status
from squid.builds.errors import BuildRevisionMismatchError
from squid.search.application import SearchService
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchPage, SearchRequest
from squid.sponsors import PublicSponsor
from squid.versions.application import VersionService
from squid.versions.domain import Edition
from squid_ui.testing import RecordingResponder, press_event
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


class EditRecorder(BuildService):
    def __init__(self, build: Build, *, conflict: bool = False) -> None:
        self.build = build
        self.conflict = conflict
        self.expected_revision: int | None = None

    def edit(
        self,
        build_id: int,
        patch: Any,
        *,
        blocking: bool = False,
        timeout: float = 30,
        expected_revision: int | None = None,
    ) -> Any:
        del patch, blocking, timeout
        assert build_id == self.build.id
        self.expected_revision = expected_revision
        recorder = self

        class Lease:
            async def __aenter__(self) -> Any:
                if recorder.conflict:
                    raise BuildRevisionMismatchError(build_id, expected_revision=expected_revision, current_revision=2)
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

            async def commit(self) -> Build:
                return replace(recorder.build, revision=recorder.build.revision + 1)

        return Lease()


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


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.PENDING, DISCORD_YELLOW),
        (Status.CONFIRMED, DISCORD_GREEN),
        (Status.DENIED, DISCORD_RED),
    ],
)
def test_build_statuses_have_exhaustive_named_colours(status: Status, expected: DiscordColour) -> None:
    assert build_handler_module._status_colour(status, build_id=7) is expected


@pytest.mark.parametrize("status", [None, cast(Status, "legacy")])
def test_missing_or_unknown_build_status_is_neutral_and_reported(
    status: Status | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        build_handler_module,
        "add_counter",
        lambda name, *, attributes: metrics.append((name, attributes)),
    )

    colour = build_handler_module._status_colour(status, build_id=7)

    assert colour is DISCORD_GREY
    assert metrics == [
        (
            "squid.build.invalid_submission_status",
            {"squid.build.status": "None" if status is None else "legacy"},
        )
    ]
    assert "Build 7 has no valid submission status" in caplog.text


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


async def test_build_editor_commits_against_the_revision_it_presented(display_build: Build) -> None:
    builds = EditRecorder(display_build)
    field = next(spec for spec in EDIT_FIELDS if spec.patch_key == "version_spec").bind(display_build)
    field.stage("1.21+")
    component = BuildEditScreen(
        display_build,
        builds,
        [field],
        authorize=AsyncMock(return_value=True),
        render_build=AsyncMock(return_value=sl.status("updated")),
        refresh_posts=AsyncMock(),
    )

    await component._apply(press_event(responder=RecordingResponder()))

    assert builds.expected_revision == display_build.revision
    assert component.saved is True


async def test_build_editor_turns_a_revision_conflict_into_a_reload_choice(display_build: Build) -> None:
    builds = EditRecorder(display_build, conflict=True)
    field = next(spec for spec in EDIT_FIELDS if spec.patch_key == "version_spec").bind(display_build)
    field.stage("1.21+")
    component = BuildEditScreen(
        display_build,
        builds,
        [field],
        authorize=AsyncMock(return_value=True),
        render_build=AsyncMock(),
        refresh_posts=AsyncMock(),
    )

    await component._apply(press_event(responder=RecordingResponder()))
    await component.projection.reload()

    assert component.saved is False
    assert "changed while you were editing" in str(component.validation_error)
    assert "Reload latest" in str(component.render())


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
