"""This module contains type hints that are used throughout the bot."""

from __future__ import annotations

from discord import TextChannel, VoiceChannel, StageChannel, Thread, DMChannel, PartialMessageable, GroupChannel

GuildMessageable = TextChannel | VoiceChannel | StageChannel | Thread
"""These are the types of channels in a guild that a message can be sent to."""

# From discord.abc, but they hid it behind TYPE_CHECKING
MessageableChannel = TextChannel | VoiceChannel | StageChannel | Thread | DMChannel | PartialMessageable | GroupChannel
