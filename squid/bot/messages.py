"""Keep recorded Discord message facts current.

Content and liveness are properties of the message itself, so they are maintained
from Discord's own events rather than discovered when some unrelated read happens
to fetch the message and find it gone.
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
        # Raw payloads are partial: an embed-only update carries no "content" key at
        # all, which is not the same as an edit that cleared the message body.
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
