"""Command entrypoints deliver isolated, owned mini-app panels."""

from typing import Any, cast

import discord
import pytest
from discord.ext import commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.layout_showcase import LayoutShowcaseCog
from squid_ui_discord.testing import ContextHarness, InteractionHarness, MessageHarness
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
    (session,) = bot.ui.sessions.active()
    assert len(session.message_roots) == count
    for root in session.message_roots:
        screen = root.component
        assert isinstance(screen, sd.Screen)
        assert screen.owner is cog
        assert screen.opening is not None
        assert screen.opening.source is ctx
        assert screen.session is session
        assert screen.parent is None
        assert screen.outcome is None
        assert root.scheduler is bot.ui.scheduler


def test_only_two_subcommands_remain() -> None:
    assert {entry.qualified_name for entry in cast(Any, LayoutShowcaseCog).__cog_commands__} == {
        "layout",
        "layout workbench",
        "layout review",
    }


async def test_live_join_is_idempotent_and_host_close_finishes_children() -> None:
    bot = make_layout_bot(topic_bus=sl.runtime.LocalTopicBus())
    cog = LayoutShowcaseCog(cast(Any, bot))
    ctx = cast(commands.Context[Any], ContextHarness(bot=bot, user_id=7).source)
    entry = next(entry for entry in cog.__cog_commands__ if entry.name == "review")
    await entry.callback(cog, ctx, "live")
    session = next(bot.ui.sessions.active())
    summary = session.root
    join = InteractionHarness(user_id=8, client=bot)
    await summary.dispatch("join", join.source)
    panel = summary.component
    assert len(panel.room.seats) == 1
    reviewer = panel.panels[8]
    assert reviewer.component.session is session
    assert reviewer.component.parent is None
    await summary.dispatch("join", InteractionHarness(user_id=8, client=bot).source)
    assert len(panel.room.seats) == 1
    assert panel.panels[8] is reviewer
    await reviewer.dispatch("approve", InteractionHarness(user_id=9, client=bot).source)
    assert panel.room.seats[0].review.decision == "Pending"
    await reviewer.dispatch("approve", InteractionHarness(user_id=8, client=bot).source)
    assert panel.room.seats[0].review.decision == "Approved"
    await summary.dispatch("close", InteractionHarness(user_id=8, client=bot).source)
    assert not summary.finished
    await summary.dispatch("close", InteractionHarness(user_id=7, client=bot).source)
    await summary.wait()
    assert reviewer.finished
    assert reviewer.followed == ()
    assert panel.room.closed
    assert await panel.wait() == sd.Completed(None)
    assert isinstance(await reviewer.component.wait(), sd.Cancelled)
