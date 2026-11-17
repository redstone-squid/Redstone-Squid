"""Mirrors must respect the NSFW status of supported Discord channel types."""

from unittest.mock import Mock

import discord
import pytest

from squid.bot.posts.starboard_renderer import _unsafe_nsfw


@pytest.mark.parametrize(
    "channel_type", [discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel]
)
@pytest.mark.parametrize(
    ("source_nsfw", "destination_nsfw"), [(False, False), (False, True), (True, False), (True, True)]
)
def test_channel_safety(channel_type: type, source_nsfw: bool, destination_nsfw: bool) -> None:
    source = Mock(spec=channel_type, is_nsfw=Mock(return_value=source_nsfw))
    destination = Mock(spec=channel_type, is_nsfw=Mock(return_value=destination_nsfw))
    assert _unsafe_nsfw(source, destination) is (source_nsfw and not destination_nsfw)


def test_private_channel_has_no_nsfw_marker() -> None:
    source = Mock(spec=discord.DMChannel)
    destination = Mock(spec=discord.TextChannel, is_nsfw=Mock(return_value=False))
    assert not _unsafe_nsfw(source, destination)
