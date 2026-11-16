"""Feedback stays public and notifies the owner only where they have access."""

from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from squid.bot import feedback_cog as feedback
from squid_i18n.text import NEUTRAL, discord_text, resolve_text
from squid_ui.ext.discord.testing import interaction_harness
from tests.support.discord import make_layout_bot
from tests.support.services import bot_services


@pytest.mark.parametrize(
    ("command", "description"),
    [
        ("bug", "Report a bug with the bot. Your message will be public."),
        ("suggestion", "Suggest improvements to the bot. Your message will be public."),
    ],
)
async def test_feedback_commands_publish_and_only_ping_owner(
    monkeypatch: pytest.MonkeyPatch, command: str, description: str
) -> None:
    bot = make_layout_bot(services=bot_services())
    bot.owner_id = 42
    cog = feedback.FeedbackCog(bot)
    interaction = interaction_harness(user_id=7)
    interaction.client = bot
    channel_send = AsyncMock()
    monkeypatch.setattr(feedback.sd, "send_to", Mock(return_value=channel_send))
    monkeypatch.setattr(feedback, "owner_can_view", AsyncMock(return_value=True))
    entry = cast(Any, getattr(cog, command))

    assert entry.description == description
    assert not entry.guild_only
    assert entry.parameters[0].required
    await entry.callback(cog, interaction, "Please fix @everyone <@99>")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    interaction.edit_original_response.assert_not_awaited()
    interaction.delete_original_response.assert_awaited_once()
    send_to = cast(Mock, feedback.sd.send_to)
    assert send_to.call_args.args == (interaction.channel,)
    assert send_to.call_args.kwargs["allowed_mentions"].to_dict() == {"users": [42], "parse": []}
    assert channel_send.await_args is not None
    payload = str(channel_send.await_args.args[0].view.to_components())
    assert "Please fix" in payload
    assert ("Bug report: Please fix" if command == "bug" else "Suggestion: Please fix") in payload
    assert "<@7>" not in payload
    assert " from " not in payload


@pytest.mark.parametrize("in_guild", [True, False])
@pytest.mark.parametrize("dm_fails", [True, False])
async def test_feedback_falls_back_to_dm_and_reports_delivery_failure(
    monkeypatch: pytest.MonkeyPatch, in_guild: bool, dm_fails: bool
) -> None:
    owner = Mock(spec=discord.User)
    bot = make_layout_bot(services=bot_services())
    bot.owner_id = 42
    monkeypatch.setattr(bot, "get_user", Mock(return_value=None), raising=False)
    fetch_user = AsyncMock(return_value=owner)
    monkeypatch.setattr(bot, "fetch_user", fetch_user, raising=False)
    cog = feedback.FeedbackCog(bot)
    request = Mock(
        user=Mock(id=7),
        guild=Mock(id=10, name="Test server") if in_guild else None,
        channel=Mock(spec=discord.TextChannel, id=20),
        respond=AsyncMock(),
        interaction=Mock(delete_original_response=AsyncMock()),
    )
    monkeypatch.setattr(feedback, "owner_can_view", AsyncMock(return_value=False))
    send = AsyncMock()
    if dm_fails:
        send.side_effect = discord.Forbidden(Mock(status=403, reason="Forbidden"), "Cannot send messages")
    channel_send = AsyncMock()
    send_to = Mock(side_effect=[channel_send, send])
    monkeypatch.setattr(feedback.sd, "send_to", send_to)
    monkeypatch.setattr(feedback, "render_payload", lambda nodes: nodes)
    monkeypatch.setattr(feedback, "text_node", lambda text: discord_text(resolve_text(text, NEUTRAL)))

    await cog._report(cast(Any, request), "Bug report", "Something broke")

    fetch_user.assert_awaited_once_with(42)
    channel_send.assert_awaited_once()
    assert channel_send.await_args is not None
    posted = channel_send.await_args.args[0][0]
    assert "Bug report: Something broke" in posted
    assert "<@7>" not in posted
    assert " from " not in posted
    assert request.respond.await_count == (1 if dm_fails else 0)
    if dm_fails:
        assert request.respond.await_args.kwargs == {"audience": "personal"}
        request.interaction.delete_original_response.assert_not_awaited()
    else:
        request.interaction.delete_original_response.assert_awaited_once()
    assert send_to.call_count == 2
    assert send_to.call_args.args == (owner,)
    assert send_to.call_args.kwargs["allowed_mentions"].to_dict() == {"parse": []}
    assert send.await_args is not None
    sent = str(send.await_args.args)
    assert "Something broke" in sent
    assert "Bug report" in sent
    assert "<@7>" not in sent
    assert " from " not in sent
    assert ("https://discord.com/channels/10/20" if in_guild else "direct message") in sent


@pytest.mark.parametrize(
    ("channel_type", "visible", "private", "manage_threads", "missing", "expected"),
    [
        (discord.DMChannel, True, False, False, False, False),
        (discord.TextChannel, True, False, False, False, True),
        (discord.TextChannel, False, False, False, False, False),
        (discord.TextChannel, True, False, False, True, False),
        (discord.Thread, True, False, False, False, True),
        (discord.Thread, True, True, False, False, True),
        (discord.Thread, True, True, False, True, False),
        (discord.Thread, True, True, True, True, True),
    ],
)
async def test_owner_access_includes_private_thread_membership(
    channel_type: type,
    visible: bool,
    private: bool,
    manage_threads: bool,
    missing: bool,
    expected: bool,
) -> None:
    channel = Mock(spec=channel_type)
    member = Mock(spec=discord.Member)
    if channel_type is not discord.DMChannel:
        channel.guild = Mock(get_member=Mock(return_value=None), fetch_member=AsyncMock(return_value=member))
        channel.permissions_for.return_value = discord.Permissions(view_channel=visible, manage_threads=manage_threads)
        if channel_type is discord.Thread:
            channel.is_private.return_value = private
            channel.fetch_member = AsyncMock()
            lookup = channel.fetch_member
        else:
            lookup = channel.guild.fetch_member
        if missing:
            lookup.side_effect = discord.NotFound(Mock(status=404, reason="Not Found"), "Unknown member")

    assert await feedback.owner_can_view(channel, 42) is expected
    if channel_type is discord.Thread and private and not manage_threads:
        channel.fetch_member.assert_awaited_once_with(42)


async def test_missing_owner_explains_feedback_is_unavailable() -> None:
    bot = make_layout_bot(services=bot_services())
    bot.owner_id = None
    cog = feedback.FeedbackCog(bot)
    request = Mock(respond=AsyncMock())

    await cog._report(cast(Any, request), "Suggestion", "Improve things")

    request.respond.assert_awaited_once_with(
        "Feedback is unavailable: no bot owner is configured.", audience="personal"
    )
