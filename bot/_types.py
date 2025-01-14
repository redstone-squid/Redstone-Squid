from __future__ import annotations

from discord import TextChannel, VoiceChannel, StageChannel, Thread

GuildMessageable = TextChannel | VoiceChannel | StageChannel | Thread
"""These are the types of channels in a guild that a message can be sent to."""
