"""Structural tests for the bot's Components V2 views."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import discord
import pytest

from squid.bot.consent import UserDataConsentView
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.submission.ui.components import get_text_input
from squid.bot.submission.ui.views import (
    BuildEditView,
    BuildSubmissionForm,
    ConfirmationView,
    SubmissionModal,
)
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildCategory, Status

if TYPE_CHECKING:
    import squid.bot.app


@pytest.fixture
def display_build() -> Build:
    return Build(
        id=7,
        submission_status=Status.PENDING,
        category=BuildCategory.DOOR,
        width=5,
        height=6,
        depth=7,
        door_width=2,
        door_height=2,
        door_depth=1,
        door_type=["Regular"],
        door_orientation_type="Door",
        component_restrictions=["Observerless"],
        creators_ign=["Builder"],
        version_spec="1.20+",
        versions=["Java 1.20"],
        image_urls=["https://example.com/build.png"],
        extra_info={},
        original_server_id=1,
        original_channel_id=2,
        original_message_id=3,
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


def test_submission_form_uses_explicit_v2_rows(display_build: Build) -> None:
    form = BuildSubmissionForm(display_build, cast(BuildService, object()))
    payload = form.to_components()

    assert form.has_components_v2()
    assert [component["type"] for component in payload] == [10, 1, 1, 1]
    assert len(payload[-1]["components"]) == 3


def test_confirmation_view_contains_prompt_and_actions() -> None:
    view = ConfirmationView("Proceed?")

    assert view.to_components()[0]["content"] == "Proceed?"
    assert len(view.to_components()[1]["components"]) == 2


def test_user_data_consent_view_discloses_storage_and_actions() -> None:
    view = UserDataConsentView(123)
    payload = view.to_components()

    assert view.has_components_v2()
    assert "Discord user ID, Minecraft UUID" in payload[0]["content"]
    assert "stores no user account information" in payload[0]["content"]
    assert [button["label"] for button in payload[1]["components"]] == ["Agree and link", "Cancel"]
    assert view.consent is None


def test_modals_wrap_text_inputs_in_labels(display_build: Build) -> None:
    submission = SubmissionModal(display_build, cast(BuildService, object()))
    field = get_text_input(display_build, "width")
    edit = BuildEditView(display_build, cast(BuildService, object()), [field]).get_modal()

    assert all(component["type"] == discord.ComponentType.label.value for component in submission.to_components())
    assert edit.to_components()[0]["type"] == discord.ComponentType.label.value
    assert edit.to_components()[0]["component"]["type"] == discord.ComponentType.text_input.value
