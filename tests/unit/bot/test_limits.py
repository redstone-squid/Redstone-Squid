"""Command budgets cover bursts, expensive actions, and all command transports."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import discord
import pytest
from discord import app_commands
from discord.ext.commands import Context

from squid.bot.app import RedstoneSquid
from squid.bot.errors import SquidCommandTree
from squid.bot.limits import CommandLimits
from squid.core.errors import RateLimitedError
from tests.support.discord import make_command_interaction
from tests.support.services import bot_services


def test_weighted_budget_rejects_without_extending_the_window() -> None:
    budget = CommandLimits()
    with patch("squid.bot.limits.time") as clock:
        clock.monotonic.return_value = 100.0
        budget.check(1, cost=4)
        clock.monotonic.return_value = 105.0
        budget.check(1, cost=4)
        budget.check(1, cost=4)
        clock.monotonic.return_value = 110.1
        with pytest.raises(RateLimitedError) as error:
            budget.check(1, cost=4)
        assert error.value.retry_after == 5
        budget.check(2, cost=12)
        clock.monotonic.return_value = 115.0
        budget.check(1, cost=4)
        with pytest.raises(RateLimitedError):
            budget.check(1)


def test_full_table_preserves_existing_users_budgets_until_expiry() -> None:
    budget = CommandLimits()
    with patch("squid.bot.limits.time") as clock:
        clock.monotonic.return_value = 100.0
        for user_id in range(4096):
            budget.check(user_id, cost=12)
        with pytest.raises(RateLimitedError):
            budget.check(4096)
        with pytest.raises(RateLimitedError):
            budget.check(0)
        clock.monotonic.return_value = 115.0
        budget.check(4096)
        assert len(budget._users) == 1


async def test_prefix_slash_context_menu_and_actions_share_one_budget() -> None:
    bot = RedstoneSquid(bot_services())
    context = cast(Context[Any], SimpleNamespace(author=SimpleNamespace(id=7), interaction=None))
    tree = cast(SquidCommandTree[discord.Client], bot.tree)
    interaction = make_command_interaction(bot, name="example", user_id=7)
    with (
        patch.object(app_commands.CommandTree, "_call", new_callable=AsyncMock) as dispatch,
        patch("squid.bot.errors.handle_interaction_error", new_callable=AsyncMock) as present,
    ):
        for _ in range(10):
            await bot._check_command_limit(context)
        await tree._call(interaction)
        # Context menus take the same tree entry point as slash commands.
        interaction.data = cast(Any, {"name": "example", "type": 3})
        await tree._call(interaction)
        assert dispatch.await_count == 2
        with pytest.raises(RateLimitedError):
            bot.command_limits.check(7, cost=4)
        await tree._call(interaction)
        present.assert_awaited_once()
        assert interaction.command_failed
        # A hybrid command's prefix check must not charge its slash invocation twice.
        hybrid = cast(Context[Any], SimpleNamespace(author=SimpleNamespace(id=7), interaction=interaction))
        assert await bot._check_command_limit(hybrid)
        autocomplete = make_command_interaction(bot, name="example", user_id=7, autocomplete=True)
        assert autocomplete.type is discord.InteractionType.autocomplete
        await tree._call(autocomplete)
        assert dispatch.await_count == 3
    await bot.close()
