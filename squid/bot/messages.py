"""Keep recorded Discord message facts current from gateway edit and delete events.

Deletion is recorded only here and by the post reconciler; a read that finds a message gone
does not tombstone it.
"""

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)


class MessageFactCog[BotT: "squid.bot.app.RedstoneSquid"](commands.Cog):
    """Refresh stored message content and tombstone deleted messages."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Raw payloads are partial: an embed-only update has no "content" key, unlike an edit that
        # cleared the body.
        if "content" not in payload.data:
            return
        await self.bot.services.messages.record_edit(payload.message_id, payload.data["content"])

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self.bot.services.messages.mark_deleted(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        for message_id in payload.message_ids:
            await self.bot.services.messages.mark_deleted(message_id)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(MessageFactCog(bot))
