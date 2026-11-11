"""Scoping of the mention-fallback listener on the shared `on_command_error` event."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands

from squid.bot.submission.commands import BuildCommands, SearchTarget


@dataclass(frozen=True, slots=True)
class Command:
    qualified_name: str = "build submit"


@dataclass(frozen=True, slots=True)
class Context:
    command: Command = Command()
    message: None = None
    bot: None = None


async def test_a_real_command_error_passes_through_untouched() -> None:
    """Ignore errors from real commands so the shared error presenter can handle them."""
    context = Context()
    error = commands.CommandInvokeError(RuntimeError("the command itself failed"))

    result = await BuildCommands.mention_fallback_search(
        cast(Any, None), cast(commands.Context[Any], cast(Any, context)), error
    )

    assert result is None


@pytest.mark.parametrize("mention", ["<@42>", "<@!42>"])
async def test_mentions_search_builds(mention: str) -> None:
    context = SimpleNamespace(
        command=None,
        message=SimpleNamespace(content=f"{mention}   width:5  "),
        bot=SimpleNamespace(user=SimpleNamespace(id=42)),
        invoked_parents=[],
        invoked_subcommand=None,
    )
    request = SimpleNamespace(defer=AsyncMock(), respond=AsyncMock())
    screen = object()
    cog = SimpleNamespace(
        ui=SimpleNamespace(request=AsyncMock(return_value=request)), _search_screen=AsyncMock(return_value=screen)
    )

    await BuildCommands.mention_fallback_search(cast(Any, cog), cast(Any, context), commands.CommandNotFound())

    cog._search_screen.assert_awaited_once_with(scope=SearchTarget.builds, query="width:5")
    request.defer.assert_awaited_once_with("public")
    request.respond.assert_awaited_once_with(screen)


async def test_non_mention_unknown_commands_are_ignored() -> None:
    context = SimpleNamespace(
        command=None,
        message=SimpleNamespace(content="!unknown"),
        bot=SimpleNamespace(user=SimpleNamespace(id=42)),
    )
    cog = SimpleNamespace(ui=SimpleNamespace(request=AsyncMock()))

    await BuildCommands.mention_fallback_search(cast(Any, cog), cast(Any, context), commands.CommandNotFound())

    cog.ui.request.assert_not_awaited()
