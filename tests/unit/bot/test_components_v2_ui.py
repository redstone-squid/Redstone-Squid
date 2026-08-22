"""Structural tests for the bot's Components V2 views."""

from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest

from squid.accounts.domain import CURRENT_CONSENT_VERSION, CreditPreview, LinkPreview
from squid.bot.consent import ConsentPromptView, LinkConsentView
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.submission.search_view import SearchResultsView
from squid.bot.submission.ui.components import get_text_input
from squid.bot.submission.ui.views import (
    BuildEditView,
    BuildSubmissionForm,
    ConfirmationView,
    SubmissionModal,
)
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildDraft, BuildLink, DoorBuild, SourceMessage, Status
from squid.search.application import SearchService
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchPage, SearchRequest
from squid.sponsors import PublicSponsor
from squid_layouts.discord.testing import commit_render, delivered_to, fake_message

if TYPE_CHECKING:
    import squid.bot.app


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
    versions = SimpleNamespace(newest=AsyncMock(return_value="Java 1.20"))
    bot = SimpleNamespace(services=SimpleNamespace(versions=versions))
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", bot), display_build)

    layout = await handler.render_layout()
    payload = layout.to_components()

    assert layout.has_components_v2()
    assert payload[0]["type"] == discord.ComponentType.container.value
    assert "Pending: Observerless 2x2 Door" in str(payload)
    assert "https://example.com/build.png" in str(payload)
    assert "https://discord.com/channels/1/2/3" in str(payload)
    assert "Submission ID: 7" in str(payload)


@pytest.mark.asyncio
async def test_build_handler_does_not_repeat_headline_component_restrictions(display_build: Build) -> None:
    versions = SimpleNamespace(newest=AsyncMock(return_value="Java 1.20"))
    bot = SimpleNamespace(services=SimpleNamespace(versions=versions))
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", bot), display_build)

    assert await handler.get_description() is None


@pytest.mark.asyncio
async def test_build_handler_surfaces_schematic_duplicate_evidence(display_build: Build) -> None:
    display_build.extra_info["schematic_duplicates"] = [
        {"build_id": 1234, "tier": "structural-match", "footprint_distance": 0.0}
    ]
    versions = SimpleNamespace(newest=AsyncMock(return_value="Java 1.20"))
    bot = SimpleNamespace(services=SimpleNamespace(versions=versions))

    payload = (
        await BuildHandler(cast("squid.bot.app.RedstoneSquid", bot), display_build).render_layout()
    ).to_components()

    assert "Review warnings" in str(payload)
    assert "Possible duplicate" in str(payload)
    assert "Build #1234 (same structure, moved or rotated)" in str(payload)


@pytest.mark.asyncio
async def test_build_handler_credits_the_sponsoring_server_and_website(display_build: Build) -> None:
    build = replace(
        display_build,
        sponsor=PublicSponsor(
            UUID("00000000-0000-4000-8000-000000000801"),
            display_name="Sponsor Network",
            address="play.example.test",
            website_url="https://example.test/",
        ),
    )
    versions = SimpleNamespace(newest=AsyncMock(return_value="Java 1.20"))
    bot = SimpleNamespace(services=SimpleNamespace(versions=versions))
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", bot), build)

    metadata = handler.get_metadata_fields()
    payload = (await handler.render_layout()).to_components()

    assert metadata["Sponsoring Server"] == "Sponsor Network"
    assert metadata["Sponsor Website"] == "https://example.test/"
    assert "Credits" in str(payload)
    assert "Sponsoring Server" in str(payload)
    assert "Sponsor Network" in str(payload)
    assert "Resources" in str(payload)
    assert "https://example.test/" in str(payload)


def test_build_handler_uses_the_sponsor_address_when_no_name_is_public(display_build: Build) -> None:
    website_url = "https://example.test/" + "a" * 700
    build = replace(
        display_build,
        sponsor=PublicSponsor(
            UUID("00000000-0000-4000-8000-000000000802"),
            address="play.example.test",
            website_url=website_url,
        ),
    )
    handler = BuildHandler(cast("squid.bot.app.RedstoneSquid", object()), build)
    metadata = handler.get_metadata_fields()

    assert metadata["Sponsoring Server"] == "play.example.test"
    assert len(metadata["Sponsor Website"]) == 512
    assert metadata["Sponsor Website"].endswith("…")


def test_submission_form_uses_explicit_v2_rows() -> None:
    draft = BuildDraft(
        door_orientation="Door",
        door_width=2,
        door_height=2,
        patterns=["Regular"],
        version_spec="1.20+",
        creators_ign=["Builder"],
    )
    form = BuildSubmissionForm(draft, cast(BuildService, object()))
    payload = form.to_components()

    assert form.has_components_v2()
    assert [component["type"] for component in payload] == [
        discord.ComponentType.container.value,
        discord.ComponentType.action_row.value,
        discord.ComponentType.action_row.value,
        discord.ComponentType.action_row.value,
    ]
    assert "Only the door type and opening size are required" in str(payload)
    assert [button["label"] for button in payload[-1]["components"]] == [
        "Edit basics",
        "Add links & details",
        "Submit for review",
        "Cancel",
    ]


def test_confirmation_view_contains_prompt_and_actions() -> None:
    view = ConfirmationView("Proceed?")
    payload = view.to_components()

    assert payload[0]["content"] == "Proceed?"
    assert [button["label"] for button in payload[1]["components"]] == ["Confirm", "Cancel"]


JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")


def _link_preview(*, credit: CreditPreview | None = None) -> LinkPreview:
    return LinkPreview(java_uuid=JAVA_UUID, username="Notch", credit=credit)


def _rendered_text(payload: dict[str, Any]) -> str:
    """Flatten a Components V2 container into the text a reader would see.

    The consent prompt is a container of text displays now rather than one blob, so assertions read
    the whole card instead of a single `content` key.
    """
    if "content" in payload:
        return str(payload["content"])
    return "\n".join(_rendered_text(child) for child in payload.get("components", []))


def test_link_consent_view_discloses_storage_and_actions() -> None:
    """The card must still name the stored categories itself.

    The full notice moved behind a button, which is only acceptable while the prompt in front of the
    user says what it will store. This is the assertion that keeps that true.
    """
    view = LinkConsentView(123, _link_preview())
    payload = view.to_components()
    rendered = _rendered_text(payload[0])

    assert view.has_components_v2()
    assert "Discord user ID" in rendered
    assert "Minecraft UUID" in rendered
    assert "Cancelling stores nothing" in rendered
    assert [button["label"] for button in payload[1]["components"]] == [
        "Agree and link",
        "Cancel",
        "Privacy notice",
    ]
    assert view.consent is None


def test_link_consent_view_previews_what_will_be_stored() -> None:
    """The point of holding the code: the prompt names the link instead of describing categories."""
    view = LinkConsentView(123, _link_preview(credit=CreditPreview("Notch", 12)))
    rendered = _rendered_text(view.to_components()[0])

    assert "Notch" in rendered
    assert str(JAVA_UUID) in rendered
    assert "<@123>" in rendered
    assert "12 builds" in rendered
    assert "becomes attributed to your account" in rendered
    assert CURRENT_CONSENT_VERSION in rendered


def test_link_consent_view_warns_that_a_contested_credit_moves_nothing() -> None:
    """The one branch where agreeing does not do what the rest of the card implies."""
    view = LinkConsentView(
        123,
        _link_preview(credit=CreditPreview("Notch", 1, held_by_public_creator_id=UUID(int=9))),
    )
    rendered = _rendered_text(view.to_components()[0])

    assert "1 build" in rendered
    assert "already credited to another creator" in rendered
    assert "moves nothing" in rendered


def test_link_consent_view_says_when_no_credit_moves() -> None:
    view = LinkConsentView(123, _link_preview())
    rendered = _rendered_text(view.to_components()[0])

    assert "No build credits **Notch** yet" in rendered


def test_consent_prompt_view_discloses_storage_without_a_link_preview() -> None:
    """The generic prompt fronts every other gated action, so it owes the same disclosure.

    Same invariant as the link card: what will be stored is named in front of the user, and only
    the full notice sits behind a button.
    """
    view = ConsentPromptView(123)
    payload = view.to_components()
    rendered = _rendered_text(payload[0])

    assert view.has_components_v2()
    assert "Discord user ID" in rendered
    assert "Cancelling stores nothing" in rendered
    assert "<@123>" in rendered
    assert CURRENT_CONSENT_VERSION in rendered
    assert [button["label"] for button in payload[1]["components"]] == ["Agree", "Cancel", "Privacy notice"]
    assert view.consent is None


def test_modals_wrap_text_inputs_in_labels(display_build: Build) -> None:
    submission = SubmissionModal(BuildDraft(), cast(BuildService, object()))
    field = get_text_input(display_build, "width")
    edit = BuildEditView(display_build, cast(BuildService, object()), [field]).get_modal()

    assert all(component["type"] == discord.ComponentType.label.value for component in submission.to_components())
    assert edit.to_components()[0]["type"] == discord.ComponentType.label.value
    assert edit.to_components()[0]["component"]["type"] == discord.ComponentType.text_input.value


def test_submission_requires_only_type_and_opening_size() -> None:
    draft = BuildDraft()
    form = BuildSubmissionForm(draft, cast(BuildService, object()))

    assert form.is_ready is False
    draft.door_orientation = "Door"
    draft.door_dimensions = (2, 2, None)
    assert form.is_ready is True


def test_change_markers_differ_only_by_fill(display_build: Build) -> None:
    """A changed field must read as more prominent, which a smaller BULLET glyph undid."""
    unchanged = get_text_input(display_build, "width")
    changed = get_text_input(display_build, "height")
    changed.modified = True
    view = BuildEditView(display_build, cast(BuildService, object()), [unchanged, changed])

    markers = [line[0] for line in view.summary_text().splitlines()]

    assert markers == ["○", "●"]


def test_optional_edit_field_is_not_marked_required(display_build: Build) -> None:
    field = get_text_input(display_build, "version_spec")

    assert field.required is False


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
    view = SearchResultsView(
        cast(SearchService, object()),
        SearchRequest("door"),
        page,
        author_id=123,
    )

    mount = view.mount()

    payload = commit_render(mount).to_components()
    select_options = payload[1]["components"][0]["options"]
    assert [option["label"] for option in select_options] == ["Smallest 2x2 door", "Fast door"]
    assert "Close" in str(payload)

    view.detail_index = view.hits.index(record)
    assert "View build" in str(commit_render(mount).to_components())


@pytest.mark.asyncio
async def test_search_timeout_visibly_disables_bound_controls() -> None:
    page = SearchPage((BuildSearchHit("8", "Fast door", "confirmed"),), total=1, next=None, prev=None)
    view = SearchResultsView(cast(SearchService, object()), SearchRequest("door"), page, author_id=123)
    mount = view.mount()
    message = fake_message()
    await mount.send(delivered_to(message))

    await mount.finish()  # what a timeout does: disable and edit through the standing handle

    message.edit.assert_awaited_once()
    disabled = message.edit.await_args.kwargs["view"]
    controls = [child for child in disabled.walk_children() if isinstance(child, discord.ui.Button | discord.ui.Select)]
    assert controls
    assert all(control.disabled for control in controls)
