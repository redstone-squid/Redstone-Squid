"""Contracts for the bot's global logging listeners."""

import logging
from dataclasses import dataclass
from typing import Any, override

import discord
import pytest
from discord.ext import commands

from squid.bot.log_cog import LoggingCog
from squid_ui.ext.discord.testing import ContextHarness
from tests.support.discord import make_layout_bot


@dataclass
class Command:
    qualified_name: str = "build submit"
    owns_errors: bool = False

    def has_error_handler(self) -> bool:
        return self.owns_errors


@dataclass
class Identity:
    id: int

    @override
    def __str__(self) -> str:
        return "SquidBot#0000"


class ErrorCog(commands.Cog):
    @override
    async def cog_command_error(self, ctx: commands.Context[Any], error: Exception) -> None:
        pass


def context(bot: Any = None) -> Any:
    source = ContextHarness(bot=bot).source
    source.channel = source.message.channel
    source.command = Command()
    source.cog = commands.Cog()
    return source


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
    """Inspect LogRecord extra fields: the default formatter omits them.

    Checking rendered text alone cannot detect arguments leaked through those fields.
    """
    cog = LoggingCog(make_layout_bot())
    ctx = context()
    ctx.command.qualified_name = "build submit"
    ctx.args = (object(), ctx, "super-secret-argument")
    ctx.kwargs = {"notes": "private-content"}
    ctx.guild = None if guild_id is None else discord.Object(guild_id)
    ctx.author.id = 789
    ctx.interaction = interaction

    with caplog.at_level(logging.INFO, logger="squid.bot.log_cog"):
        await cog.log_command_usage(ctx)

    (record,) = caplog.records
    assert record.getMessage() == "Discord command invoked"
    assert _squid_fields(record) == {
        "squid.command.name": "build submit",
        "squid.guild.id": guild_id,
        "squid.discord.interaction": interaction is not None,
    }


async def test_ready_log_reports_bot_identity_and_guild_count(caplog: pytest.LogCaptureFixture) -> None:
    bot = make_layout_bot(user=Identity(111), guilds=[discord.Object(1), discord.Object(2)])
    cog = LoggingCog(bot)

    with caplog.at_level(logging.INFO, logger="squid.bot.log_cog"):
        await cog.log_on_ready()

    (record,) = caplog.records
    assert record.getMessage() == "Discord gateway ready, logged in as SquidBot#0000"
    assert _squid_fields(record) == {"squid.discord.bot_id": 111, "squid.discord.guild_count": 2}


async def test_command_errors_reach_the_shared_handler_only_when_unowned() -> None:
    """Local handlers own their replies and an unknown command needs no notice."""
    bot = make_layout_bot()
    cog = LoggingCog(bot)

    command_owned = context(bot)
    command_owned.command.owns_errors = True
    await cog.log_command_error(command_owned, commands.CommandError("boom"))

    cog_owned = context(bot)
    cog_owned.cog = ErrorCog()
    await cog.log_command_error(cog_owned, commands.CommandError("boom"))

    unknown = context(bot)
    unknown.command = None
    unknown.cog = None
    await cog.log_command_error(unknown, commands.CommandNotFound("nope"))

    for source in (command_owned, cog_owned, unknown):
        assert not source.sends
        assert not source.replies

    unowned = context(bot)
    await cog.log_command_error(unowned, commands.CommandError("boom"))

    (reply,) = (*unowned.sends, *unowned.replies)
    assert "Something went wrong" in str(reply.kwargs["view"].to_components())
