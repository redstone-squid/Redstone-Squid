"""Structural tests for the bot's semantic Components V2 workflows."""

from dataclasses import replace
from typing import cast, override
from uuid import UUID

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission import build_card as build_card_module
from squid.bot.submission.build_card import build_description, build_metadata_fields, render_build_card
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.ui.build_edit import BuildEditScreen
from squid.bot.ui import DISCORD_GREEN, DISCORD_GREY, DISCORD_RED, DISCORD_YELLOW, DiscordColour, render_payload
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.application import BuildService
from squid.builds.application.edits import build_target
from squid.builds.domain import Status
from squid.builds.models import Build, BuildLink, DoorBuild
from squid.edits.domain import Revision, RevisionConflictError
from squid.messages.domain import MessageRef
from squid.search.application import SearchService
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchPage, SearchRequest
from squid.sponsors import PublicSponsor
from squid_ui_discord import SessionSpec
from squid_ui_discord.testing import (
    AsyncCallRecorder,
    assert_within_limits,
    commit_render,
    delivered_to,
    interaction_harness,
    message_harness,
)
from tests.support.build_edit_runner import BuildEditRecorder, build_edit_actor, edit_workspace
from tests.support.discord import make_layout_bot


class SearchRecorder(SearchService):
    def __init__(self) -> None:
        self.calls: list[SearchRequest] = []

    @override
    async def search(self, request: SearchRequest) -> SearchPage:
        self.calls.append(request)
        raise AssertionError("the seeded first page must not be fetched again")


class BuildRecorder(BuildService):
    def __init__(self) -> None:
        pass


class EditRecorder(BuildService):
    def __init__(self, build: Build, *, conflict: bool = False) -> None:
        self.runner = BuildEditRecorder(
            build,
            error=RevisionConflictError(Revision(build_target(build.id or 7), build.revision), 2) if conflict else None,
        )

    @override
    async def get(self, build_id: int) -> Build:
        assert build_id == self.runner.build.id
        return self.runner.build


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
        source_messages=(MessageRef(id=3, guild_id=1, channel_id=2),),
    )


@pytest.mark.asyncio
async def test_build_card_renders_composable_v2_card(display_build: Build) -> None:
    build = display_build

    presentation = render_payload([await render_build_card(build, MediaPreviewClient())])
    payload = presentation.layout.to_components()

    assert presentation.layout.has_components_v2()
    assert payload[0]["type"] == discord.ComponentType.container.value
    assert "Pending: Observerless 2x2 Door" in str(payload)
    assert "https://example.com/build.png" in str(payload)
    assert "https://discord.com/channels/1/2/3" in str(payload)
    assert "Original Discord message" in str(payload)
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
    assert build_card_module._status_colour(status, build_id=7) is expected


@pytest.mark.parametrize("status", [None, cast(Status, "legacy")])
def test_missing_or_unknown_build_status_is_neutral_and_reported(
    status: Status | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        build_card_module,
        "add_counter",
        lambda name, *, attributes: metrics.append((name, attributes)),
    )

    colour = build_card_module._status_colour(status, build_id=7)

    assert colour is DISCORD_GREY
    assert metrics == [
        (
            "squid.build.invalid_submission_status",
            {"squid.build.status": "None" if status is None else "legacy"},
        )
    ]
    assert "Build 7 has no valid submission status" in caplog.text


async def test_build_editor_shows_the_edit_form_sections(display_build: Build) -> None:
    component = BuildEditScreen(
        display_build,
        BuildRecorder(),
        edit_workspace(display_build),
        edits=BuildEditRecorder(),
        actor=build_edit_actor,
        authorize=AsyncCallRecorder(result=True),
        render_build=AsyncCallRecorder(),
        refresh_posts=AsyncCallRecorder(),
    )

    # `projection` is an atomic resource, so reading its status aborts a discovery render
    # until it has settled. A mount settles it before rendering; calling `render()` straight
    # from a test has to do the same. The loader returns the seed the constructor supplied,
    # so this touches no service.
    await component.projection.reload()

    rendered = str(component.render())
    assert "Observerless" in rendered
    assert "Not in the catalogue" not in rendered
    assert "Open new editor" not in str(component.render())
    assert "build-edit-recovery" not in str(component.render())
    assert component._current()[0] is display_build


def test_build_editor_declares_keyed_topic_following_policy() -> None:
    assert isinstance(BuildEditScreen.__response_spec__.session, SessionSpec)
    assert BuildEditScreen.__response_spec__.session.name == "build-edit"
    assert BuildEditScreen.__response_spec__.timeout == 900
    assert BuildEditScreen.__response_spec__.live_updates is True
    assert BuildEditScreen.__response_spec__.retain_routed_on_timeout is True


async def test_expired_build_editor_retains_only_the_fresh_recovery_route(display_build: Build) -> None:
    component = BuildEditScreen(
        display_build,
        BuildRecorder(),
        edit_workspace(display_build),
        edits=BuildEditRecorder(),
        actor=build_edit_actor,
        authorize=AsyncCallRecorder(result=True),
        render_build=AsyncCallRecorder(result=sl.status("card")),
        refresh_posts=AsyncCallRecorder(),
    )
    bot = make_layout_bot()
    interaction = interaction_harness(user_id=7)
    interaction.client = bot
    result = await (await sd.request(interaction)).respond(component)
    assert isinstance(result, sd.Presented)
    message_root = result.root
    assert message_root.retain_routed_on_timeout is True
    assert not component.expired
    active = interaction.response.send_message.await_args.kwargs["view"]
    assert all(
        item.label != "Open new editor" for item in active.walk_children() if isinstance(item, discord.ui.Button)
    )

    await message_root.handle_timeout()

    assert component.expired
    expired = interaction.edit_original_response.await_args.kwargs["view"]
    buttons = {item.label: item.disabled for item in expired.walk_children() if isinstance(item, discord.ui.Button)}
    assert buttons == {"Open new editor": False}
    assert any(
        "Unsaved changes were discarded" in item.content
        for item in expired.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    )


async def test_build_editor_commits_against_the_revision_it_presented(
    display_build: Build, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = EditRecorder(display_build)
    workspace = edit_workspace(display_build)
    assert workspace.prefill({"version_compatibility": "1.21+"}) is None
    component = BuildEditScreen(
        display_build,
        builds,
        workspace,
        edits=builds.runner,
        actor=build_edit_actor,
        authorize=AsyncCallRecorder(result=True),
        render_build=AsyncCallRecorder(result=sl.status("updated")),
        refresh_posts=AsyncCallRecorder(),
    )

    async with sd.testing.stage(component, owner=object()) as ui:
        # A topic refresh must not advance the revision of the values being edited.
        component.projection.replace((replace(display_build, revision=99), sl.status("Latest card")))
        await ui.press("review")
        await ui.press("apply")
        await ui.press("close")
        assert await component.wait() == sd.Completed(display_build.id)
        assert ui.root.finished

    assert builds.runner.commands[0].expected[0].value == 1
    assert component.saved is True


async def test_closing_build_editor_cancels_without_saving(display_build: Build) -> None:
    builds = EditRecorder(display_build)
    component = BuildEditScreen(
        display_build,
        builds,
        edit_workspace(display_build),
        edits=builds.runner,
        actor=build_edit_actor,
        authorize=AsyncCallRecorder(result=True),
        render_build=AsyncCallRecorder(result=sl.status("card")),
        refresh_posts=AsyncCallRecorder(),
    )

    async with sd.testing.stage(component, owner=object()) as ui:
        await ui.press("close")
        assert await component.wait() == sd.Cancelled(sd.ScreenCancelReason.USER)
        assert ui.root.finished

    assert builds.runner.commands == []
    assert not component.saved


async def test_build_editor_turns_a_revision_conflict_into_a_reload_choice(
    display_build: Build, monkeypatch: pytest.MonkeyPatch
) -> None:
    builds = EditRecorder(display_build, conflict=True)
    workspace = edit_workspace(display_build)
    assert workspace.prefill({"version_compatibility": "1.21+"}) is None
    component = BuildEditScreen(
        display_build,
        builds,
        workspace,
        edits=builds.runner,
        actor=build_edit_actor,
        authorize=AsyncCallRecorder(result=True),
        render_build=AsyncCallRecorder(),
        refresh_posts=AsyncCallRecorder(),
    )

    async with sd.testing.stage(component, owner=object()) as ui:
        await ui.press("review")
        await ui.press("apply")

        assert component.saved is False
        assert component.conflict
        assert "changed while you were editing" in str(component.render())
        assert "Reload latest" in str(component.render())
        assert component.outcome is None
        assert not ui.root.finished


def test_build_card_does_not_repeat_headline_component_restrictions(display_build: Build) -> None:
    build = display_build

    assert build_description(build) is None


def test_build_card_shows_the_submitter_description_escaped(display_build: Build) -> None:
    build = replace(display_build, description="Uses *two* observers")

    assert build_description(build) == "\nUses \\*two\\* observers"


def test_build_card_credits_the_sponsoring_server_and_website(display_build: Build) -> None:
    build = replace(
        display_build,
        sponsor=PublicSponsor(
            UUID("00000000-0000-4000-8000-000000000801"),
            display_name="Sponsor Network",
            address="play.example.test",
            website_url="https://example.test/",
        ),
    )

    metadata = build_metadata_fields(build)

    assert metadata["Sponsoring Server"] == "Sponsor Network"
    assert metadata["Sponsor Website"] == "https://example.test/"


async def test_build_card_renders_bounded_human_attachment_evidence(display_build: Build) -> None:
    display_build.extra_info["schematic_duplicates"] = [
        {
            "build_id": 7,
            "title": "2x2 Seamless Door",
            "tier": "identical",
            "footprint_distance": 0.0,
            "source_attachments": [
                {"attachment_id": "a", "filename": "alternate.litematic"},
                {"attachment_id": "b", "filename": "second.litematic"},
                {"attachment_id": "c", "filename": "third.litematic"},
                {"attachment_id": "d", "filename": "fourth.litematic"},
            ],
        }
        for _ in range(8)
    ]
    display_build.extra_info["attachment_failures"] = [
        {
            "attachment_id": str(index),
            "filename": f"broken-{index}.litematic",
            "stage": "analysis",
            "detail": "bad\nnews " + "x" * 2_000,
        }
        for index in range(8)
    ]
    build = display_build

    metadata = build_metadata_fields(build)
    presentation = render_payload([await render_build_card(build, MediaPreviewClient())])

    duplicate = metadata["⚠ Possible duplicate"]
    failure = metadata["⚠ Attachment issues"]
    assert "2x2 Seamless Door (#7)" in duplicate
    assert "alternate.litematic" in duplicate
    assert "and 2 more" in duplicate
    assert "…and 3 more" in duplicate
    assert "broken-0.litematic — bad news" in failure
    assert "…and 4 more" in failure
    assert len(duplicate) <= 1_000
    assert len(failure) <= 1_000
    assert "Attachment issues" in str(presentation.layout.to_components())
    assert_within_limits(presentation.layout)


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
    assert "Close" not in str(rendered.to_components())


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


async def test_build_history_survives_a_failed_card_refresh(display_build: Build) -> None:
    builds = EditRecorder(display_build)
    before = display_build.version_spec
    workspace = edit_workspace(display_build)
    assert workspace.prefill({"version_compatibility": "1.21+"}) is None

    async def unavailable_card(build: Build) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        raise RuntimeError("card service unavailable")

    component = BuildEditScreen(
        display_build,
        builds,
        workspace,
        edits=builds.runner,
        actor=build_edit_actor,
        authorize=AsyncCallRecorder(result=True),
        render_build=unavailable_card,
        refresh_posts=AsyncCallRecorder(),
    )
    async with sd.testing.stage(component, owner=object()) as ui:
        await ui.press("review")
        await ui.press("apply")
        assert component.saved
        assert component.controller.refresh_failed
        assert component.controller.undoable
        assert builds.runner.build.version_spec == "1.21+"
        assert await component.controller.undo() is not None
        assert builds.runner.build.version_spec == before
        assert component.controller.redoable
        # A change outside this session invalidates redo even if its field is unchanged.
        builds.runner.build.description = "someone else's change"
        builds.runner.build.revision += 1
        with pytest.raises(RevisionConflictError):
            await component.controller.redo()
        assert builds.runner.build.description == "someone else's change"
        assert builds.runner.build.version_spec == before
