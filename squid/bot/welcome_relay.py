"""Relay select welcome messages to a general discussion channel."""

import logging
from typing import TYPE_CHECKING

import discord
from discord import AllowedMentions
from discord.ext.commands import Cog

from squid.bot._types import GuildMessageable
from squid.bot.ui import render_payload, text_node
from squid.community.domain import WelcomeRelayDecision, WelcomeRelayPolicy
from squid.settings.domain.runtime import RuntimeSettings
from squid_i18n import Text, mention
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class WelcomeRelay[BotT: squid.bot.app.RedstoneSquid](Cog):
    """Listens for built-in welcome messages and occasionally mirrors them elsewhere."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.service = bot.services.welcome_relay

    async def _settings(self) -> RuntimeSettings:
        settings = await self.bot.services.settings.get_runtime()
        self.service.configure(
            WelcomeRelayPolicy(settings.welcome_channel_id, settings.welcome_forward_chance),
            enabled=settings.welcome_enabled,
        )
        return settings

    @Cog.listener(name="on_message")
    async def maybe_forward_welcome_message(self, message: discord.Message):
        """Forward some welcome system messages to the general channel."""
        if message.type is not discord.MessageType.new_member:
            return
        settings = await self._settings()
        if not settings.welcome_enabled:
            return
        decision = self.service.record_message(
            channel_id=message.channel.id,
            is_new_member_message=message.type is discord.MessageType.new_member,
            system_content=message.system_content,
        )
        if decision is not None:
            await self._forward(decision, settings.welcome_relay_channel_id)

    @Cog.listener(name="on_member_join")
    async def track_new_member(self, member: discord.Member):
        """Track a new member who joined, so we can match them to the welcome message later."""
        if member.guild.id != self.bot.owner_server_id:
            return
        settings = await self._settings()
        if not settings.welcome_enabled:
            return
        decision = self.service.record_join(member.id, member.name)
        if decision is not None:
            await self._forward(decision, settings.welcome_relay_channel_id)

    async def _forward(self, decision: WelcomeRelayDecision, channel_id: int) -> None:
        general_channel = await self.bot.get_or_fetch_messageable_channel(channel_id)
        if not isinstance(general_channel, GuildMessageable):
            logger.warning("General channel %s is not messageable", channel_id)
            return

        # Text keeps the mention structured; a "<@id>" string would be escaped as literal text.
        content = Text.join(mention(decision.member_id), decision.system_content.split(decision.matched_name))
        await send_to(
            general_channel,
            allowed_mentions=AllowedMentions(users=False, roles=False, everyone=False, replied_user=False),
        )(render_payload([text_node(content)]))


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(WelcomeRelay(bot))
