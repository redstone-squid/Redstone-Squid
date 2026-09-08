"""Channel union types shared across the bot."""

from discord import DMChannel, GroupChannel, PartialMessageable, StageChannel, TextChannel, Thread, VoiceChannel

GuildMessageable = TextChannel | VoiceChannel | StageChannel | Thread

# Same union as discord.abc.MessageableChannel, which discord.py defines only under TYPE_CHECKING.
MessageableChannel = TextChannel | VoiceChannel | StageChannel | Thread | DMChannel | PartialMessageable | GroupChannel
