"""Discord process entry-point lifecycle tests."""

from pytest_mock import MockerFixture

from squid.bot import app as bot_app


async def test_main_owns_observability_and_logging_shutdown(mocker: MockerFixture) -> None:
    config = mocker.Mock()
    config.discord.token.get_secret_value.return_value = "discord-token"
    listener = mocker.Mock()
    handle = mocker.Mock()
    runtime = mocker.MagicMock()
    runtime.__aenter__.return_value = runtime
    bot = mocker.MagicMock()
    running_bot = mocker.MagicMock()
    running_bot.start = mocker.AsyncMock()
    bot.__aenter__.return_value = running_bot
    mocker.patch.object(bot_app, "configure_bot_logging", return_value=listener)
    configure = mocker.patch.object(bot_app, "configure_observability", return_value=handle)
    mocker.patch.object(bot_app, "create_application_runtime", return_value=runtime)
    mocker.patch.object(bot_app, "RedstoneSquid", return_value=bot)

    await bot_app.main(config)

    configure.assert_called_once_with(config.observability, service_name="bot")
    running_bot.start.assert_awaited_once_with("discord-token")
    handle.shutdown.assert_called_once_with()
    listener.stop.assert_called_once_with()
