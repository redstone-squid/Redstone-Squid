"""Scoping of the mention-fallback listener on the shared `on_command_error` event."""

from types import SimpleNamespace
from typing import Any, cast

from discord.ext import commands

from squid.bot.submission.search import SearchCog


async def test_a_real_command_error_passes_through_untouched() -> None:
    """Every command error in the bot lands here, and only CommandNotFound belongs to us.

    Raising instead — as an unconditional `assert ctx.command is None` did — fails inside
    `on_command_error` itself, so discord.py drops the error that was being reported.
    """
    context = SimpleNamespace(command=SimpleNamespace(qualified_name="build submit"), message=None, bot=None)
    error = commands.CommandInvokeError(RuntimeError("the command itself failed"))

    result = await SearchCog.mention_fallback_search(
        cast(Any, None), cast(commands.Context[Any], cast(Any, context)), error
    )

    assert result is None
