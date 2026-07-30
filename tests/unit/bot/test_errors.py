"""Discord error adapter tests."""

from unittest.mock import patch

import pytest
from discord.ext import commands

from squid.bot.errors import (
    build_error_presentation,
    handle_interaction_error,
    handle_message_error,
    is_error_presented,
    unwrap_error,
)
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import InternalError
from tests.helpers.discord import make_interaction, make_message


def test_unwrap_error_finds_original_command_exception() -> None:
    original = BuildNotFoundError(42)

    assert unwrap_error(commands.CommandInvokeError(original)) is original


def test_domain_error_presentation_exposes_only_public_detail() -> None:
    error = BuildNotFoundError(42)

    presentation = build_error_presentation(error)

    assert presentation.title == "Resource not found"
    assert presentation.detail == "Build not found. Check the build ID and try again."
    assert presentation.error_id is None


def test_unexpected_error_presentation_redacts_diagnostic_detail() -> None:
    presentation = build_error_presentation(InternalError("database password leaked"))

    assert "database password leaked" not in presentation.detail
    assert presentation.error_id is not None
    assert presentation.error_id in presentation.detail


@pytest.mark.asyncio
async def test_interaction_error_uses_initial_ephemeral_response() -> None:
    harness = make_interaction()

    await handle_interaction_error(harness.interaction, BuildNotFoundError(42), surface="context_menu")

    harness.send_initial.assert_awaited_once()
    initial_call = harness.send_initial.await_args
    assert initial_call is not None
    assert initial_call.kwargs["ephemeral"] is True
    layout = initial_call.kwargs["view"]
    assert layout.has_components_v2()
    assert "Build not found. Check the build ID and try again." in str(layout.to_components())
    assert "embed" not in initial_call.kwargs
    harness.send_followup.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_error_uses_followup_after_response() -> None:
    harness = make_interaction(response_done=True, guild_id=3)

    await handle_interaction_error(harness.interaction, BuildNotFoundError(42), surface="view")

    harness.send_initial.assert_not_awaited()
    harness.send_followup.assert_awaited_once()
    followup_call = harness.send_followup.await_args
    assert followup_call is not None
    assert followup_call.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_presented_error_is_not_rendered_or_logged_twice() -> None:
    error = InternalError("private diagnostic")
    message = make_message()
    interaction = make_interaction()

    with patch("squid.bot.errors.logger.error") as log_error:
        await handle_message_error(message.message, error)
        await handle_interaction_error(interaction.interaction, error, surface="command")

    message.edit.assert_awaited_once()
    edit_call = message.edit.await_args
    assert edit_call is not None
    assert edit_call.kwargs["content"] is None
    assert edit_call.kwargs["embed"] is None
    assert edit_call.kwargs["view"].has_components_v2()
    interaction.send_initial.assert_not_awaited()
    log_error.assert_called_once()
    assert is_error_presented(error)
