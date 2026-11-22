"""Check feedback channel access and owner configuration."""

from unittest.mock import AsyncMock, Mock

import discord
import pytest

from squid.bot import feedback_cog as feedback


@pytest.mark.parametrize(
    ("channel_type", "visible", "private", "manage_threads", "missing", "expected"),
    [
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
