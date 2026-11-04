"""Channel type unions shared by Discord transport helpers."""

from discord import DMChannel, GroupChannel, PartialMessageable, StageChannel, TextChannel, Thread, VoiceChannel

GuildMessageable = TextChannel | VoiceChannel | StageChannel | Thread
"""These are the types of channels in a guild that a message can be sent to."""

# discord.abc exposes this union only under TYPE_CHECKING; runtime checks need a local value.
MessageableChannel = TextChannel | VoiceChannel | StageChannel | Thread | DMChannel | PartialMessageable | GroupChannel
