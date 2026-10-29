"""Discord error adapter tests."""

from collections.abc import AsyncIterator, Iterator
from contextlib import nullcontext

import discord
import pytest
from discord import app_commands
from discord.ext import commands
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.accounts.errors import MinecraftServiceUnavailableError
from squid.bot.errors import (
    SquidCommandTree,
    _safe_log_context,
    build_error_notice,
    handle_interaction_error,
    is_error_presented,
    record_operation_error,
    unwrap_error,
)
from squid.bot.utils.permissions import PermissionNodeRequired
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import InternalError
from squid.observability import TraceSpan, bind_correlation_id, correlation_id, unbind_correlation_id
from squid_ui.text import Localization, current_localization
from tests.support.discord import make_command_interaction, make_interaction, make_layout_bot, make_message


@pytest.fixture
async def command_tree() -> AsyncIterator[SquidCommandTree[discord.Client]]:
    async def localize(_source: object) -> Localization:
        return Localization(locale="en-GB")

    async with discord.Client(intents=discord.Intents.none()) as client:
        runtime = sd.install(client, localization=localize)
        try:
            yield SquidCommandTree(client)
        finally:
            await runtime.close()


@pytest.fixture
def error_correlation() -> Iterator[str]:
    identifier = "b" * 32
    token = bind_correlation_id(identifier)
    try:
        yield identifier
    finally:
        unbind_correlation_id(token)


def test_unwrap_error_finds_original_command_exception() -> None:
    original = BuildNotFoundError(42)

    assert unwrap_error(commands.CommandInvokeError(original)) is original


def test_log_context_redaction_recurses_through_supported_containers() -> None:
    context: dict[str, object] = {
        "discord_id": 1,
        "DISCORD_USER_ID": 2,
        "user_id": 3,
        "owner_discord_id": 4,
        "safe": [
            {"discord_id": 5, "job_id": 6, 8: "non-string mapping keys are not safe diagnostic fields"},
            ({"user_id": 7, "state": "ready"},),
        ],
    }

    assert _safe_log_context(context) == {
        "safe": [
            {"job_id": 6},
            ({"state": "ready"},),
        ]
    }


def test_domain_error_presentation_exposes_only_public_detail() -> None:
    error = BuildNotFoundError(42)

    notice = build_error_notice(error)

    assert notice.title == "Resource not found"
    assert notice.detail == "Build not found. Check the build ID and try again."
    assert notice.error_id is None


def test_minecraft_service_outage_is_rendered_as_a_stable_retryable_bot_error() -> None:
    notice = build_error_notice(MinecraftServiceUnavailableError(), locale="en-GB")

    assert notice.title == "Service unavailable"
    assert notice.detail == "The Minecraft account service is temporarily unavailable. Try again in a few minutes."
    assert notice.error_id is None


def test_unexpected_error_presentation_redacts_diagnostic_detail(error_correlation: str) -> None:
    notice = build_error_notice(InternalError("database password leaked"))

    assert "database password leaked" not in notice.detail
    assert notice.error_id == error_correlation
    # The card shows the short reference, since the user has to retype it; the full id stays on
    # the log line and the stored report.
    assert notice.reference == "b" * 12
    assert notice.detail.count("b" * 12) == 1
    assert "b" * 13 not in notice.detail


@pytest.mark.parametrize(
    ("error", "title"),
    [
        (PermissionNodeRequired(("build.submission.approve",)), "Missing permission"),
        (PermissionNodeRequired(("bot.tree.sync",), forbidden=True), "Permission withheld"),
    ],
)
def test_permission_errors_name_the_missing_nodes(error: Exception, title: str) -> None:
    assert build_error_notice(error).title == title


@pytest.mark.parametrize("failed", [False, True], ids=["success", "failure"])
async def test_command_dispatch_scopes_context_and_records_only_safe_span_attributes(
    command_tree: SquidCommandTree[discord.Client], mocker: MockerFixture, failed: bool
) -> None:
    seen: list[tuple[str, str | None]] = []
    group = app_commands.Group(name="admin", description="Administration")

    @group.command(name="records")
    async def records(interaction: discord.Interaction) -> None:
        seen.append((correlation_id(), current_localization().locale))
        assert correlation_id() == seen[0][0], "all work in the invocation must share one correlation ID"
        interaction.command_failed = failed

    command_tree.add_command(group)
    interaction = make_command_interaction(
        command_tree.client,
        name="admin",
        options=[{"name": "records", "type": 1, "options": []}],
        guild_id=10,
    )
    span = mocker.Mock(spec=TraceSpan)
    trace = mocker.patch("squid.bot.errors.trace_span", return_value=nullcontext(span))

    await command_tree._call(interaction)  # pyright: ignore[reportPrivateUsage]

    trace.assert_called_once_with(
        "discord.command admin records",
        {
            "squid.command.name": "admin records",
            "squid.surface": "application_command",
            "squid.guild.id": 10,
            "squid.channel.id": 20,
        },
    )
    assert len(seen) == 1
    assert seen[0][1] == "en-GB"
    assert correlation_id() != seen[0][0], "the correlation binding must end with the invocation"
    assert current_localization().locale is None
    if failed:
        span.set_error.assert_called_once_with()
    else:
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


async def test_unexpected_error_redacts_reports_logs_and_response_without_mutating_context(
    error_correlation: str, caplog: pytest.LogCaptureFixture
) -> None:
    reports = RecordingReports()
    discord_id = 987654321098765432
    error = InternalError(
        "database password leaked",
        context={
            "discord_id": discord_id,
            "minecraft_uuid": "11111111-1111-1111-1111-111111111111",
            "attempts": [{"resolved_by_discord_id": discord_id, "job_id": 17}],
        },
    )
    harness = make_interaction(user_id=discord_id, guild_id=3, channel_id=4, error_reports=reports)

    await handle_interaction_error(harness.interaction, error, surface="command")

    (report,) = reports.calls
    assert report["correlation_id"] == error_correlation
    assert report["reference"] == error_correlation[:12]
    assert report["surface"] == "command"
    safe_context = {
        "command": None,
        "guild_id": 3,
        "channel_id": 4,
    }
    safe_application_context = {
        "minecraft_uuid": "11111111-1111-1111-1111-111111111111",
        "attempts": [{"job_id": 17}],
    }
    assert report["context"] == {**safe_context, "application_context": safe_application_context}
    (record,) = [record for record in caplog.records if record.name == "squid.bot.errors"]
    assert str(discord_id) not in record.getMessage()
    assert repr(safe_context) in record.getMessage()
    assert repr(safe_application_context) in record.getMessage()
    assert error_correlation in record.getMessage()
    assert error.context["discord_id"] == discord_id
    (response,) = harness.send_initial.records
    rendered = str(response.kwargs["view"].to_components())
    assert "database password leaked" not in rendered
    assert error_correlation[:12] in rendered
    assert error_correlation[:13] not in rendered


async def test_a_failing_report_store_still_answers_the_user(caplog: pytest.LogCaptureFixture) -> None:
    """The handler already owes a response; losing the diagnostic must not cost the reply too."""
    harness = make_interaction(error_reports=RecordingReports(failing=True))

    with caplog.at_level("ERROR"):
        await handle_interaction_error(harness.interaction, InternalError("boom"), surface="command")

    harness.send_initial.assert_awaited_once()
    assert "Could not capture a Discord failure" in caplog.text


async def test_unknown_application_command_uses_bounded_fallback_name(
    command_tree: SquidCommandTree[discord.Client], mocker: MockerFixture
) -> None:
    interaction = make_command_interaction(command_tree.client, name="unregistered")
    trace = mocker.patch("squid.bot.errors.trace_span", return_value=nullcontext(TraceSpan()))

    with pytest.raises(app_commands.CommandNotFound):
        await command_tree._call(interaction)  # pyright: ignore[reportPrivateUsage]

    assert trace.call_args.args[0] == "discord.command unknown"
    assert trace.call_args.args[1]["squid.command.name"] == "unknown"


async def test_autocomplete_dispatch_does_not_emit_a_command_span(
    command_tree: SquidCommandTree[discord.Client], mocker: MockerFixture
) -> None:
    @command_tree.command(name="settings")
    async def settings(interaction: discord.Interaction, value: str) -> None:
        pytest.fail("autocomplete must not invoke the command body")

    @settings.autocomplete("value")
    async def values(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        assert current == "part"
        return [app_commands.Choice(name="partial", value="partial")]

    interaction = make_command_interaction(
        command_tree.client,
        name="settings",
        autocomplete=True,
        options=[{"name": "value", "type": 3, "value": "part", "focused": True}],
    )
    send = mocker.patch.object(discord.InteractionResponse, "autocomplete", new_callable=mocker.AsyncMock)
    trace = mocker.patch("squid.bot.errors.trace_span")

    await command_tree._call(interaction)  # pyright: ignore[reportPrivateUsage]

    send.assert_awaited_once()
    assert send.await_args is not None
    assert send.await_args.args[0][0].value == "partial"
    trace.assert_not_called()


async def test_application_command_error_records_exception_and_answers_the_user(
    command_tree: SquidCommandTree[discord.Client], mocker: MockerFixture
) -> None:
    reports = RecordingReports()
    harness = make_interaction(error_reports=reports)
    error = app_commands.AppCommandError("failed")
    record = mocker.patch("squid.bot.errors.record_current_exception")

    await command_tree.on_error(harness.interaction, error)

    record.assert_called_once_with(error)
    (report,) = reports.calls
    assert report["surface"] == "application_command"
    assert report["error"] is error
    harness.send_initial.assert_awaited_once()


async def test_domain_error_uses_initial_ephemeral_response_without_recording_a_failure() -> None:
    reports = RecordingReports()
    harness = make_interaction(error_reports=reports)

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
    assert reports.calls == []


async def test_interaction_error_uses_followup_after_response() -> None:
    harness = make_interaction(response_done=True, guild_id=3)

    await handle_interaction_error(harness.interaction, BuildNotFoundError(42), surface="view")

    harness.send_initial.assert_not_awaited()
    harness.send_followup.assert_awaited_once()
    followup_call = harness.send_followup.await_args
    assert followup_call is not None
    assert followup_call.kwargs["ephemeral"] is True


async def test_interaction_error_completes_a_public_defer_in_place() -> None:
    """A public "thinking" placeholder is edited, not answered with an ephemeral follow-up."""
    harness = sd.testing.InteractionHarness(client=make_layout_bot())
    request = await sd.request(harness.source)
    await request.defer("public")

    await handle_interaction_error(harness.source, BuildNotFoundError(42), surface="command")

    harness.edit_original_response.assert_awaited_once()
    harness.followup.send.assert_not_awaited()


async def test_presented_error_is_not_rendered_or_logged_twice(caplog: pytest.LogCaptureFixture) -> None:
    error = InternalError("private diagnostic")
    message = make_message()
    interaction = make_interaction()

    await record_operation_error(
        error,
        locale=None,
        result=sd.delivery.DeliveryResult(message.message, None),
        presented=True,
    )
    await handle_interaction_error(interaction.interaction, error, surface="command")

    message.edit.assert_not_awaited()
    interaction.send_initial.assert_not_awaited()
    (record,) = [record for record in caplog.records if record.name == "squid.bot.errors"]
    assert "surface=command_operation" in record.getMessage()
    assert is_error_presented(error)
