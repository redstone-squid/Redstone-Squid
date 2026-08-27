"""Discord process entry-point lifecycle tests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
from discord.ext.commands import Bot, Context
from pytest_mock import MockerFixture

import squid_ui_discord as sd
from squid.bot import app as bot_app


async def test_main_owns_observability_and_logging_shutdown(mocker: MockerFixture) -> None:
    config = mocker.Mock()
    config.discord.token.get_secret_value.return_value = "discord-token"
    # Off: this test owns a mocked supervisor, which would accept the drain coroutine and never
    # await it. `test_main_starts_log_capture` covers the wiring instead.
    config.diagnostics.capture_logged_errors = False
    listener = mocker.Mock()
    handle = mocker.Mock()
    runtime = mocker.MagicMock()
    runtime.__aenter__.return_value = runtime
    bot = mocker.MagicMock()
    bot.start = mocker.AsyncMock()
    bot.__aenter__.return_value = bot
    mocker.patch.object(bot_app, "configure_bot_logging", return_value=listener)
    configure = mocker.patch.object(bot_app, "configure_observability", return_value=handle)
    mocker.patch.object(bot_app, "create_bot_runtime", return_value=runtime)
    mocker.patch.object(bot_app, "RedstoneSquid", return_value=bot)
    mocker.patch.object(bot_app, "ProcessHealthServer", return_value=mocker.MagicMock())

    await bot_app.main(config)

    configure.assert_called_once_with(config.observability, service_name="bot")
    bot.start.assert_awaited_once_with("discord-token")
    handle.shutdown.assert_called_once_with()
    listener.stop.assert_called_once_with()


async def test_main_starts_log_capture(mocker: MockerFixture) -> None:
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
    mocker.patch.object(bot_app, "configure_bot_logging", return_value=mocker.Mock())
    mocker.patch.object(bot_app, "configure_observability", return_value=mocker.Mock())
    mocker.patch.object(bot_app, "create_bot_runtime", return_value=runtime)
    mocker.patch.object(bot_app, "RedstoneSquid", return_value=bot)
    mocker.patch.object(bot_app, "ProcessHealthServer", return_value=mocker.MagicMock())
    start = mocker.patch.object(bot_app, "start_log_capture")

    await bot_app.main(config)

    start.assert_called_once_with(
        bot.background_tasks,
        runtime.services.error_reports,
        enabled=True,
        capacity=16,
    )


async def test_prefix_invoke_establishes_invocation_scope(mocker: MockerFixture) -> None:
    bot = bot_app.RedstoneSquid.__new__(bot_app.RedstoneSquid)
    runtime = sd.install(cast(discord.Client, bot))
    context = cast(
        Context[Any],
        SimpleNamespace(
            bot=bot,
            author=SimpleNamespace(id=7),
            guild=None,
            interaction=None,
            send=AsyncMock(),
        ),
    )
    seen: list[sd.Invocation] = []

    async def invoke(_bot: object, source: Context[Any]) -> None:
        invocation = await sd.Invocation.of(source)
        assert sd.current_invocation() is invocation
        seen.append(invocation)

    mocker.patch.object(Bot, "invoke", new=invoke)

    await bot_app.RedstoneSquid.invoke(bot, context)
    await runtime.close()

    assert len(seen) == 1
    assert sd.current_invocation() is None
