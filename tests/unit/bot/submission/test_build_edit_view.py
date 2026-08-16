"""Confirmation and result rendering of the `/build edit` workspace."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest

from squid.bot.submission.ui import views
from squid.bot.submission.ui.components import get_text_input
from squid.bot.submission.ui.views import BuildEditView
from squid.builds.application import BuildService
from squid.builds.domain import OtherBuild, Status


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.messages: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return self.deferred or bool(self.messages)

    async def defer(self, **kwargs: Any) -> None:
        self.deferred = True

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.message = AsyncMock(spec=discord.WebhookMessage)

    async def send(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return self.message


class _FakeInteraction:
    """An interaction on an ephemeral component message, where `message.edit` 404s."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.message = AsyncMock(spec=discord.Message)
        self.edits: list[dict[str, Any]] = []

    async def edit_original_response(self, **kwargs: Any) -> Any:
        self.edits.append(kwargs)
        return self.message


class _AcceptedConfirmation:
    """Stand-in for the prompt the user confirms."""

    def __init__(self, prompt: str | None = None, timeout: int = 60, *, locale: str | None = None) -> None:
        self.value = True

    async def wait(self) -> bool:
        return True


async def test_saved_changes_are_rendered_through_the_interaction_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ephemeral workspace is only reachable through the interaction, never `Message.edit`."""
    monkeypatch.setattr(views, "ConfirmationView", _AcceptedConfirmation)
    build = OtherBuild(submission_status=Status.PENDING)
    field = get_text_input(build, "version_spec")
    field.modified = True
    field.actual_value = "1.21"
    field.current_string_value = "1.21"
    builds = AsyncMock(spec=BuildService)
    client = SimpleNamespace(
        for_build=lambda _build: SimpleNamespace(
            render_container=AsyncMock(return_value=discord.ui.Container(discord.ui.TextDisplay("card")))
        )
    )
    view = BuildEditView(build, cast(BuildService, builds), [field])
    interaction = _FakeInteraction(client)

    await view.submit.callback(cast(discord.Interaction, cast(Any, interaction)))

    # The response slot is spent on a deferred update, which is what keeps the original
    # response pointing at the workspace message for the final edit.
    assert interaction.response.deferred is True
    assert interaction.response.messages == []
    assert len(interaction.followup.sent) == 1
    interaction.followup.message.delete.assert_awaited_once()
    builds.save.assert_awaited_once_with(build)
    interaction.message.edit.assert_not_awaited()
    assert "Changes saved" in str(interaction.edits[-1]["view"].to_components())
