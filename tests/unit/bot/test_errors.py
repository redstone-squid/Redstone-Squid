"""Discord error adapter tests."""

from unittest.mock import patch

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from pytest_mock import MockerFixture

import squid_layouts as sl
from squid.bot.errors import (
    SquidCommandTree,
    build_error_presentation,
    handle_interaction_error,
    is_error_presented,
    record_operation_error,
    unwrap_error,
)
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import InternalError
from squid.observability import correlation_id
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
    assert presentation.error_id == "b" * 32
    # The card shows the short reference, since the user has to retype it; the full id stays on
    # the log line and the stored report.
    assert presentation.reference == "b" * 12
    assert presentation.detail.count("b" * 12) == 1
    assert "b" * 13 not in presentation.detail


@pytest.mark.parametrize(
    ("error", "title"),
    [
        (PermissionNodeRequired(("build.submission.approve",)), "Missing permission"),
        (PermissionNodeRequired(("bot.tree.sync",), forbidden=True), "Permission withheld"),
    ],
)
def test_permission_errors_name_the_missing_nodes(error: Exception, title: str) -> None:
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


class RecordingReports:
    """Stand-in for the error report service, optionally a failing one."""

    def __init__(self, *, failing: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self._failing = failing

    async def record(self, error: BaseException, **kwargs: object) -> None:
        if self._failing:
            msg = "the database is down"
            raise RuntimeError(msg)
        self.calls.append({"error": error, **kwargs})


async def test_unexpected_error_is_captured_with_a_redacted_context() -> None:
    """Persisting is more exposing than logging, so the stored context is the redacted one."""
    reports = RecordingReports()
    discord_id = 987654321098765432
    error = InternalError("database password leaked", context={"discord_id": discord_id, "job_id": 17})
    harness = make_interaction(user_id=discord_id, guild_id=3, channel_id=4, error_reports=reports)

    with patch("squid.bot.errors.correlation_id", return_value="b" * 32):
        await handle_interaction_error(harness.interaction, error, surface="command")

    (call,) = reports.calls
    assert call["correlation_id"] == "b" * 32
    assert call["reference"] == "b" * 12
    assert call["surface"] == "command"
    context = call["context"]
    assert isinstance(context, dict)
    assert str(discord_id) not in repr(context)
    assert context["application_context"] == {"job_id": 17}


async def test_a_domain_error_is_not_captured() -> None:
    """A build that does not exist is explained to the user in full and is not a failure."""
    reports = RecordingReports()
    harness = make_interaction(error_reports=reports)

    await handle_interaction_error(harness.interaction, BuildNotFoundError(1), surface="command")

    assert reports.calls == []


async def test_a_failing_report_store_still_answers_the_user(caplog: pytest.LogCaptureFixture) -> None:
    """The handler already owes a response; losing the diagnostic must not cost the reply too."""
    harness = make_interaction(error_reports=RecordingReports(failing=True))

    with caplog.at_level("ERROR"):
        await handle_interaction_error(harness.interaction, InternalError("boom"), surface="command")

    harness.send_initial.assert_awaited_once()
    assert "Could not capture a Discord failure" in caplog.text


async def test_application_command_binds_one_correlation_id_for_the_whole_invocation(
    mocker: MockerFixture,
) -> None:
    """Log lines a command emits before failing must share the ID its error card shows.

    Before this binding the ID was minted at presentation time, so nothing the command had
    already logged carried it and the reference resolved to a traceback with no context.
    """
    client = discord.Client(intents=discord.Intents.none())
    tree = SquidCommandTree(client)
    interaction = mocker.Mock()
    interaction.type = discord.InteractionType.application_command
    interaction.data = {"name": "settings"}
    interaction.guild_id = None
    interaction.channel_id = None
    interaction.command_failed = False

    seen: list[str] = []

    async def record_bound(_tree: object, _interaction: object) -> None:
        seen.append(correlation_id())

    mocker.patch.object(app_commands.CommandTree, "_call", new=record_bound)

    await tree._call(interaction)  # pyright: ignore[reportPrivateUsage]
    outside = correlation_id()

    assert len(seen) == 1
    assert seen[0] != outside, "the binding must not leak past the invocation"


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
        await record_operation_error(
            error,
            locale=None,
            receipt=sl.discord.delivery.DeliveryReceipt(message.message, None),
            presented=True,
        )
        await handle_interaction_error(interaction.interaction, error, surface="command")

    message.edit.assert_not_awaited()
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
    assert log_call.args[4] == {"command": None, "guild_id": 3, "channel_id": 4}
    assert log_call.args[5] == {
        "minecraft_uuid": "11111111-1111-1111-1111-111111111111",
        "attempts": [{"job_id": 17}],
    }
    rendered_log = log_call.args[0] % log_call.args[1:]
    assert str(discord_id) not in rendered_log
    assert error.context["discord_id"] == discord_id
