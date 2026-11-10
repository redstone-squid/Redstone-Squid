"""Command entrypoints deliver isolated, owned mini-app panels."""

from typing import Any, cast

import discord
import pytest
from discord.ext import commands

import squid_ui as sl
from squid.bot.layout_showcase import LayoutShowcaseCog
from squid_ui_discord.testing import ContextHarness, MessageHarness
from tests.support.discord import make_layout_bot


@pytest.mark.parametrize(("command", "count"), [("workbench", 2), ("review", 3)])
async def test_commands_deliver_live_panels(command: str, count: int) -> None:
    bot = make_layout_bot(topic_bus=sl.runtime.LocalTopicBus())
    cog = LayoutShowcaseCog(cast(Any, bot))
    harness = ContextHarness(message=MessageHarness(message_id=1), bot=bot, user_id=7)
    ctx = cast(commands.Context[Any], harness.source)
    entry = next(entry for entry in cog.__cog_commands__ if entry.name == command)
    await entry.callback(cog, ctx)
    assert harness.source.send.await_count == count
    for call in harness.source.send.await_args_list:
        assert isinstance(call.kwargs["view"], discord.ui.LayoutView)


def test_only_two_subcommands_remain() -> None:
    assert {entry.qualified_name for entry in LayoutShowcaseCog.__cog_commands__} == {
        "layout",
        "layout workbench",
        "layout review",
    }
