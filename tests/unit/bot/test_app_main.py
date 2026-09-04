"""Discord process entry-point lifecycle tests."""

from dataclasses import dataclass, field
from typing import Any, cast

import discord
from discord.ext.commands import Bot, Context
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.bot import app as bot_app
from squid_ui.text import Localization, current_localization
from squid_ui_discord.testing import AsyncCallRecorder, ContextHarness, MessageHarness


@dataclass(frozen=True)
class SetupServices:
    error_reports: object
    permission_epoch: object


@dataclass
class TreeRecorder:
    set_translator: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


@dataclass(frozen=True)
class UIRuntimeRecorder:
    job: Any

    def run(self) -> Any:
        return self.job


async def test_run_bot_starts_log_capture(mocker: MockerFixture) -> None:
    """Worker-style absorbed failures are only stored because this is started."""
    config = mocker.Mock()
    config.discord.token.get_secret_value.return_value = "discord-token"
    config.diagnostics.capture_logged_errors = True
    config.diagnostics.log_capture_queue = 16
    runtime = mocker.MagicMock()
    runtime.__aenter__.return_value = runtime
    bot = mocker.MagicMock()
    bot.start = mocker.AsyncMock()
    bot.__aenter__.return_value = bot
    mocker.patch.object(bot_app, "create_bot_runtime", return_value=runtime)
    mocker.patch.object(bot_app, "RedstoneSquid", return_value=bot)
    mocker.patch.object(bot_app, "ProcessHealthServer", return_value=mocker.MagicMock())
    start = mocker.patch.object(bot_app, "start_log_capture")

    await bot_app._run_bot(config)

    start.assert_called_once_with(
        bot.background_tasks,
        runtime.services.error_reports,
        enabled=True,
        capacity=16,
    )


async def test_prefix_invoke_establishes_localization_scope(mocker: MockerFixture) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    runtime = sd.install(
        cast(discord.Client, bot),
        localization=lambda _source: _localization("en-GB"),
    )
    bot.ui = cast(Any, runtime)
    context = cast(Context[Any], ContextHarness(message=MessageHarness(), bot=bot, user_id=7).source)
    context.command = None
    context.guild = None
    context.channel = None  # type: ignore[assignment]
    context.command_failed = False
    seen: list[str | None] = []
    span = mocker.Mock()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = span
    trace = mocker.patch.object(bot_app, "trace_span", return_value=span_context)

    async def invoke(_bot: object, source: Context[Any]) -> None:
        del source
        seen.append(current_localization().locale)

    mocker.patch.object(Bot, "invoke", new=invoke)

    await bot_app.RedstoneSquid.invoke(bot, context)
    await runtime.close()

    assert seen == ["en-GB"]
    assert current_localization().locale is None
    assert trace.call_args.args == (
        "discord.command unknown",
        {"squid.command.name": "unknown", "squid.surface": "prefix_command"},
    )
    span.set_error.assert_not_called()


async def test_failed_prefix_command_marks_its_invocation_span(mocker: MockerFixture) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    scope = mocker.Mock()
    scope.resolve = mocker.AsyncMock(return_value=mocker.Mock(localization=Localization(locale="en-GB")))
    bot.ui = mocker.Mock()
    bot.ui.scope.return_value = scope
    context = mocker.Mock(
        command=mocker.Mock(qualified_name="admin sync"),
        command_failed=True,
        guild=None,
        channel=None,
    )
    mocker.patch.object(Bot, "invoke", new=mocker.AsyncMock())
    span = mocker.Mock()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = span
    trace = mocker.patch.object(bot_app, "trace_span", return_value=span_context)

    await bot_app.RedstoneSquid.invoke(bot, context)

    assert trace.call_args.args[0] == "discord.command admin sync"
    span.set_error.assert_called_once_with()


async def _localization(locale: str) -> Localization:
    return Localization(locale=locale)


async def test_setup_hook_supervises_the_layout_runtime_as_one_job(mocker: MockerFixture) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    bot.background_tasks = mocker.Mock()
    bot.__dict__["services"] = SetupServices(error_reports=object(), permission_epoch=object())
    bot.__dict__["_BotBase__tree"] = TreeRecorder()
    bot.database_config = None
    bot.topic_bridge = None
    bot.development_mode = False
    bot.__dict__["layout_profiler"] = object()
    bot.load_extension = AsyncCallRecorder()
    run = AsyncCallRecorder()
    layout_job = run()
    bot.__dict__["ui"] = UIRuntimeRecorder(layout_job)
    router = mocker.Mock()
    mocker.patch.object(bot_app, "EXTENSIONS", ())
    mocker.patch.object(bot_app, "control_router", router)
    mocker.patch.object(bot_app, "start_permission_epoch_watch")

    try:
        await bot_app.RedstoneSquid.setup_hook(bot)

        bot.background_tasks.start.assert_called_once_with(layout_job, name="layout-runtime")
        router.register.assert_called_once_with(bot)
    finally:
        layout_job.close()
