"""Scoping of the mention-fallback listener on the shared `on_command_error` event."""

from dataclasses import dataclass
from typing import Any, cast

from discord.ext import commands

from squid.bot.submission.search import SearchCog


@dataclass(frozen=True, slots=True)
class Command:
    qualified_name: str = "build submit"


@dataclass(frozen=True, slots=True)
class Context:
    command: Command = Command()
    message: None = None
    bot: None = None


async def test_a_real_command_error_passes_through_untouched() -> None:
    """Every command error in the bot lands here, and only CommandNotFound belongs to us.

    Raising instead — as an unconditional `assert ctx.command is None` did — fails inside
    `on_command_error` itself, so discord.py drops the error that was being reported.
    """
    context = Context()
    error = commands.CommandInvokeError(RuntimeError("the command itself failed"))

    result = await SearchCog.mention_fallback_search(
        cast(Any, None), cast(commands.Context[Any], cast(Any, context)), error
    )

    assert result is None
