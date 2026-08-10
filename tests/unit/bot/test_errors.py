"""Discord error adapter tests."""

from unittest.mock import patch

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from pytest_mock import MockerFixture

from squid.bot.errors import (
    SquidCommandTree,
    build_error_presentation,
    handle_interaction_error,
    handle_message_error,
    is_error_presented,
    unwrap_error,
)
from squid.bot.utils.permissions import (
    GlobalAdministratorRequired,
    HomeServerTrustedOrGlobalAdministratorRequired,
    ServerAdministratorRequired,
    TrustedOrGlobalAdministratorRequired,
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
    with patch("squid.bot.errors.correlation_id", return_value="b" * 32):
        presentation = build_error_presentation(InternalError("database password leaked"))

    assert "database password leaked" not in presentation.detail
    assert presentation.error_id is not None
    assert presentation.error_id == "b" * 32
    assert presentation.error_id in presentation.detail


@pytest.mark.parametrize(
    ("error", "title"),
    [
        (GlobalAdministratorRequired(), "Global administrator only"),
        (ServerAdministratorRequired(), "Server administrator only"),
        (TrustedOrGlobalAdministratorRequired(), "Trusted role required"),
        (HomeServerTrustedOrGlobalAdministratorRequired(), "Command unavailable"),
    ],
)
def test_permission_errors_identify_the_required_tier(error: Exception, title: str) -> None:
    assert build_error_presentation(error).title == title


async def test_application_command_span_excludes_user_id(mocker: MockerFixture) -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = SquidCommandTree(client)
    interaction = mocker.Mock()
    interaction.type = discord.InteractionType.application_command
    interaction.data = {
        "name": "admin",
        "options": [{"name": "records", "type": 1, "options": []}],
    }
    interaction.guild_id = 10
    interaction.channel_id = 20
    interaction.command_failed = False
    base_call = mocker.patch.object(app_commands.CommandTree, "_call", new=mocker.AsyncMock())
    span = mocker.Mock()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = span
    trace = mocker.patch("squid.bot.errors.trace_span", return_value=span_context)

    await tree._call(interaction)  # pyright: ignore[reportPrivateUsage]

    base_call.assert_awaited_once_with(interaction)
    attributes = trace.call_args.args[1]
    assert attributes == {
        "squid.command.name": "admin records",
        "squid.surface": "application_command",
        "squid.guild.id": 10,
        "squid.channel.id": 20,
    }
    assert all("user" not in name for name in attributes)
    span.set_error.assert_not_called()


async def test_application_command_failure_marks_span(mocker: MockerFixture) -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = SquidCommandTree(client)
    interaction = mocker.Mock(
        type=discord.InteractionType.application_command,
        data={"name": "submit"},
        guild_id=None,
        channel_id=20,
        command_failed=True,
    )
    mocker.patch.object(app_commands.CommandTree, "_call", new=mocker.AsyncMock())
    span = mocker.Mock()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = span
    mocker.patch("squid.bot.errors.trace_span", return_value=span_context)

    await tree._call(interaction)  # pyright: ignore[reportPrivateUsage]

    span.set_error.assert_called_once_with()


async def test_application_command_error_records_exception_on_current_span(mocker: MockerFixture) -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = SquidCommandTree(client)
    interaction = mocker.Mock()
    error = app_commands.AppCommandError("failed")
    record = mocker.patch("squid.bot.errors.record_current_exception")
    handle = mocker.patch("squid.bot.errors.handle_interaction_error", new=mocker.AsyncMock())

    await tree.on_error(interaction, error)

    record.assert_called_once_with(error)
    handle.assert_awaited_once_with(interaction, error, surface="application_command")


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


@pytest.mark.asyncio
async def test_unexpected_error_log_excludes_discord_account_identifiers() -> None:
    discord_id = 8_675_309
    error = InternalError(
        "private diagnostic",
        context={
            "discord_id": discord_id,
            "minecraft_uuid": "11111111-1111-1111-1111-111111111111",
            "attempts": [
                {
                    "resolved_by_discord_id": discord_id,
                    "job_id": 17,
                }
            ],
        },
    )
    interaction = make_interaction(user_id=discord_id, guild_id=3, channel_id=4)

    with (
        patch("squid.bot.errors.correlation_id", return_value="b" * 32),
        patch("squid.bot.errors.logger.error") as log_error,
    ):
        await handle_interaction_error(interaction.interaction, error, surface="command")

    log_error.assert_called_once()
    log_call = log_error.call_args
    assert log_call is not None
    assert log_call.args[3] == {"command": None, "guild_id": 3, "channel_id": 4}
    assert log_call.args[4] == {
        "minecraft_uuid": "11111111-1111-1111-1111-111111111111",
        "attempts": [{"job_id": 17}],
    }
    rendered_log = log_call.args[0] % log_call.args[1:]
    assert str(discord_id) not in rendered_log
    assert error.context["discord_id"] == discord_id
