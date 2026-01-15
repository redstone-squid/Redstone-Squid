"""Relay select welcome messages to a general discussion channel."""

import asyncio
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
    forward_chance: Final[float] = 1 / 10

    def __init__(self, bot: BotT):
        self.bot = bot
        self.random = random.Random()
        self.pending_members: dict[str, discord.Member] = {}
        """Maps user names(!) to members who recently joined.

        This is because the discord welcome message only contains the user name, not the full member object.
        """

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

        await asyncio.sleep(5)  # Wait a bit to ensure the member is already cached from on_member_join
        for member_name, member in self.pending_members.items():  # noqa: B007
            if member_name in message.system_content:
                break
        else:
            logger.warning("Could not find member for welcome message: %s", message.system_content)
            return

        await general_channel.send(
            message.system_content.replace(member_name, member.mention),
            allowed_mentions=AllowedMentions(users=False, roles=False, everyone=False, replied_user=False),
        )

    @Cog.listener(name="on_member_join")
    async def track_new_member(self, member: discord.Member):
        """Track a new member who joined, so we can match them to the welcome message later."""
        self.pending_members[member.name] = member


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(WelcomeRelay(bot))
