"""Relay select welcome messages to a general discussion channel."""

import logging
import random
from typing import TYPE_CHECKING, Final

import discord
from discord import AllowedMentions
from discord.ext.commands import Cog

from squid.bot._types import GuildMessageable

if TYPE_CHECKING:
    import squid.bot

logger = logging.getLogger(__name__)


class WelcomeRelay[BotT: "squid.bot.RedstoneSquid"](Cog):
    """Listens for built-in welcome messages and occasionally mirrors them elsewhere."""

    welcome_channel_id: Final[int] = 1356094722531393680
    general_channel_id: Final[int] = 433618741528625155
    forward_chance: Final[float] = 0.2

    def __init__(self, bot: BotT):
        self.bot = bot
        self.random = random.Random()

    @Cog.listener(name="on_message")
    async def maybe_forward_welcome_message(self, message: discord.Message):
        """Forward some welcome system messages to the general channel."""

        if message.channel.id != self.welcome_channel_id:
            return

        if message.type is not discord.MessageType.new_member:
            return

        if self.random.random() >= self.forward_chance:
            return

        general_channel = self.bot.get_channel(self.general_channel_id)
        if general_channel is None:
            general_channel = await self.bot.fetch_channel(self.general_channel_id)

        if not isinstance(general_channel, GuildMessageable):
            logger.warning("General channel %s is not messageable", self.general_channel_id)
            return

        await general_channel.send(
            message.system_content,
            allowed_mentions=AllowedMentions(users=False, roles=False, everyone=False, replied_user=False),
        )


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(WelcomeRelay(bot))
