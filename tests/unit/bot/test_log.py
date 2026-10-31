"""Contracts for the bot's global logging listeners."""

import logging
from unittest.mock import Mock

import pytest
from discord.ext import commands
from pytest_mock import MockerFixture

from squid.bot.log import LoggingCog


def _squid_fields(record: logging.LogRecord) -> dict[str, object]:
    """The structured fields the cog attached, without `LogRecord`'s own attributes."""
    return {name: value for name, value in record.__dict__.items() if name.startswith("squid.")}


@pytest.mark.parametrize(
    ("guild_id", "interaction"),
    [(456, None), (None, object())],
    ids=["guild-prefix", "dm-interaction"],
)
async def test_command_log_carries_only_low_cardinality_fields(
    caplog: pytest.LogCaptureFixture, guild_id: int | None, interaction: object | None
) -> None:
    """The exact field set is the contract, so comparing it whole is the assertion.

    `log_command_usage` is handed the entire invocation context, raw argument values
    included. A membership check over the rendered text would not prove they stay out:
    `extra` fields are not rendered by the default formatter, so a leaked argument
    would pass one. Equality over the attached fields plus a constant message does.
    """
    cog = LoggingCog(Mock())
    ctx = Mock()
    ctx.command.qualified_name = "build submit"
    ctx.args = (object(), ctx, "super-secret-argument")
    ctx.kwargs = {"notes": "private-content"}
    ctx.guild = None if guild_id is None else Mock(id=guild_id)
    ctx.author.id = 789
    ctx.interaction = interaction

    with caplog.at_level(logging.INFO, logger="squid.bot.log"):
        await cog.log_command_usage(ctx)

    (record,) = caplog.records
    assert record.getMessage() == "Discord command invoked"
    assert _squid_fields(record) == {
        "squid.command.name": "build submit",
        "squid.guild.id": guild_id,
        "squid.discord.interaction": interaction is not None,
    }


async def test_ready_log_reports_bot_identity_and_guild_count(caplog: pytest.LogCaptureFixture) -> None:
    """`on_ready` fires again on every failed RESUME, so it must stay identifier-free."""
    bot = Mock()
    bot.user.id = 111
    bot.guilds = [Mock(), Mock()]
    cog = LoggingCog(bot)

    with caplog.at_level(logging.INFO, logger="squid.bot.log"):
        await cog.log_on_ready()

    (record,) = caplog.records
    assert record.getMessage() == "Discord gateway ready"
    assert _squid_fields(record) == {"squid.discord.bot_id": 111, "squid.discord.guild_count": 2}


async def test_command_errors_reach_the_shared_handler_only_when_unowned(mocker: MockerFixture) -> None:
    """A local handler wins, and an unknown command is not an error worth presenting.

    Presenting twice is the failure this guards: the shared handler renders a user-facing
    message, so running it alongside a command's own handler double-replies.
    """
    handle = mocker.patch("squid.bot.log.handle_context_error", new=mocker.AsyncMock())
    cog = LoggingCog(Mock())

    command_owned = Mock()
    command_owned.command.has_error_handler.return_value = True
    await cog.log_command_error(command_owned, commands.CommandError("boom"))

    cog_owned = Mock()
    cog_owned.command.has_error_handler.return_value = False
    cog_owned.cog.has_error_handler.return_value = True
    await cog.log_command_error(cog_owned, commands.CommandError("boom"))

    unknown = Mock()
    unknown.command = None
    unknown.cog = None
    await cog.log_command_error(unknown, commands.CommandNotFound("nope"))

    handle.assert_not_awaited()

    unowned = Mock()
    unowned.command.has_error_handler.return_value = False
    unowned.cog.has_error_handler.return_value = False
    error = commands.CommandError("boom")
    await cog.log_command_error(unowned, error)

    handle.assert_awaited_once_with(unowned, error)
