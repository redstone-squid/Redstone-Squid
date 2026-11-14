"""Discord process entry-point lifecycle tests."""

from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self, cast

import anyio
import discord
import pytest
from discord.ext.commands import Bot, Context
from discord.ext.commands.view import StringView
from pydantic import SecretStr
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.bot import app as bot_app
from squid.config import BotProcessConfig, DatabaseConfig, DiagnosticsConfig, DiscordConfig, VerificationConfig
from squid.runtime import ApplicationRuntime, BackgroundTaskSupervisor
from squid_reactivity import EventLedger, transaction
from squid_ui.text import Localization, current_localization
from squid_ui_discord.devtools import DevTools
from squid_ui_discord.testing import AsyncCallRecorder, ContextHarness, MessageHarness
from tests.support.runtime import SchedulingRecorder
from tests.support.services import bot_services


@dataclass
class TreeRecorder:
    def get_commands(self) -> list[Any]:
        return []

    set_translator: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


@dataclass(frozen=True)
class UIRuntimeRecorder:
    job: Any

    def run(self) -> Any:
        return self.job


class ProcessBot(bot_app.RedstoneSquid):
    def __init__(self) -> None:
        self.background_tasks = BackgroundTaskSupervisor()
        self.starts: list[str] = []

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.starts.append(token)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        pass


@dataclass
class ClosingResource:
    close: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)
    aclose: AsyncCallRecorder = field(default_factory=AsyncCallRecorder)


async def test_run_bot_starts_log_capture(mocker: MockerFixture) -> None:
    """Background jobs can log an exception and continue; log capture turns those failures into reports."""
    config = BotProcessConfig.model_construct(
        discord=DiscordConfig(token=SecretStr("discord-token")),
        database=DatabaseConfig(url=SecretStr("postgresql+asyncpg://localhost/test")),
        verification=VerificationConfig(code_pepper=SecretStr("test-pepper")),
        diagnostics=DiagnosticsConfig(capture_logged_errors=True, log_capture_queue=16),
    )
    runtime = ApplicationRuntime(bot_services(), AsyncCallRecorder(), AsyncCallRecorder())
    bot = ProcessBot()
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
    runtime = sd.install(cast(discord.Client, bot), sd.Config(localization=lambda _source: _localization("en-GB")))
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
    runtime = sd.install(cast(discord.Client, bot), sd.Config(localization=lambda _source: _localization("en-GB")))
    bot.ui = cast(Any, runtime)
    context = cast(Context[Any], ContextHarness(message=MessageHarness(), bot=bot, user_id=7).source)
    context.command = mocker.Mock(qualified_name="admin sync")
    context.guild = None
    context.channel = None  # type: ignore[assignment]
    context.command_failed = True
    mocker.patch.object(Bot, "invoke", new=AsyncCallRecorder())
    span = mocker.Mock()
    span_context = mocker.MagicMock()
    span_context.__enter__.return_value = span
    trace = mocker.patch.object(bot_app, "trace_span", return_value=span_context)

    await bot_app.RedstoneSquid.invoke(bot, context)
    await runtime.close()

    assert trace.call_args.args[0] == "discord.command admin sync"
    span.set_error.assert_called_once_with()


@pytest.mark.parametrize("replace", [False, True])
async def test_devtools_dispatch_and_dashboard_lifetime(replace: bool) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    Bot.__init__(bot, command_prefix="!", intents=discord.Intents.none())
    await bot._async_setup_hook()
    bot._reaction_subscriptions = {}
    bot.ui = sd.install(bot)
    check = AsyncCallRecorder(result=True)
    cog = DevTools(bot, check=check)
    await bot.add_cog(cog)
    message = cast(Any, MessageHarness())
    message._state = bot._connection
    message.author = ContextHarness(user_id=7).author
    message.guild = None
    message.attachments = []
    context = Context(
        message=message,
        bot=bot,
        view=StringView("roots"),
        prefix="!",
        command=cog.ui_command,
        invoked_with="ui",
    )
    send = AsyncCallRecorder(result=message)
    context.send = cast(Any, send)

    try:
        await bot.invoke(context)
        assert not context.command_failed
        check.assert_awaited_once_with(context)
        send.assert_awaited_once()
        request = await sd.request(context)
        assert request.scope is cog.ui
        roots = list(cog.ui._lifetime.roots())
        assert len(roots) == 1
        assert roots[0].snapshot().access == sd.Owner(7)

        if replace:
            replacement = DevTools(bot, check=check)
            await bot.add_cog(replacement, override=True)
            assert not replacement.ui.closed
        else:
            assert await bot.remove_cog(cog.qualified_name) is cog
        assert cog.ui.closed
        assert roots[0].finished
        with pytest.raises(RuntimeError, match="no live UI scope"):
            await sd.request(context)
    finally:
        await bot.remove_cog(cog.qualified_name)
        await bot.ui.close()
        await Bot.close(bot)


async def _localization(locale: str) -> Localization:
    return Localization(locale=locale)


@dataclass
class ControlRouterRecorder:
    clients: list[object] = field(default_factory=list)

    def register(self, client: object) -> None:
        self.clients.append(client)


async def test_setup_hook_supervises_the_layout_runtime_as_one_job(mocker: MockerFixture) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    bot.background_tasks = SchedulingRecorder()
    bot.__dict__["services"] = bot_services()
    bot.__dict__["_BotBase__tree"] = TreeRecorder()
    bot.database_config = None
    bot.topic_bridge = None
    bot.development_mode = False
    bot.__dict__["layout_profiler"] = object()
    bot.load_extension = AsyncCallRecorder()
    run = AsyncCallRecorder()
    layout_job = run()
    bot.__dict__["ui"] = UIRuntimeRecorder(layout_job)
    router = ControlRouterRecorder()
    mocker.patch.object(bot_app, "EXTENSIONS", ())
    mocker.patch.object(bot_app, "control_router", router)
    mocker.patch.object(bot_app, "start_permission_epoch_watch")

    try:
        await bot_app.RedstoneSquid.setup_hook(bot)

        assert cast(SchedulingRecorder, bot.background_tasks).starts == [(layout_job, "layout-runtime")]
        assert router.clients == [bot]
    finally:
        layout_job.close()


async def test_ui_cleanup_failure_still_closes_application_resources(mocker: MockerFixture) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    bot.reactions = cast(Any, ClosingResource())
    bot.ui = cast(
        Any, ClosingResource(close=AsyncCallRecorder(error=ExceptionGroup("UI shutdown", [ValueError("cleanup")])))
    )
    bot.background_tasks = cast(Any, ClosingResource())
    bot.topic_bridge = None
    bot.catbox = cast(Any, ClosingResource())
    bot.media_previews = cast(Any, ClosingResource())
    close_gateway = mocker.patch.object(Bot, "close", new=AsyncCallRecorder())

    await bot.close()

    bot.background_tasks.close.assert_awaited_once()
    bot.catbox.aclose.assert_awaited_once()
    bot.media_previews.aclose.assert_awaited_once()
    close_gateway.assert_awaited_once()


@pytest.mark.parametrize("development", [True, False])
async def test_start_owns_diagnostic_observation(mocker: MockerFixture, development: bool) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    bot.development_mode = development
    bot.action_ledger = EventLedger()

    async def work() -> None:
        with transaction("child"):
            pass

    async def start(self: Bot, token: str, *, reconnect: bool = True) -> None:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(work)
        raise RuntimeError("connection closed")

    mocker.patch.object(Bot, "start", start)
    with pytest.raises(RuntimeError, match="connection closed"):
        await bot.start("test-token")
    with transaction("outside"):
        pass
    assert [result.name for result in bot.action_ledger.results] == (["child"] if development else [])
