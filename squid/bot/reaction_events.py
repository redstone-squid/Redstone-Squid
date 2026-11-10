"""Load Discord reaction listeners without reloading the shared routing types."""

from typing import TYPE_CHECKING, override

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import squid.bot.app


class ReactionRouterCog(commands.Cog):
    """Own the bot's only Discord raw-reaction listeners."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.bot.reactions.dispatch_add(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.bot.reactions.dispatch_remove(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self.bot.reactions.dispatch_clear(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self.bot.reactions.dispatch_clear_emoji(payload)

    @override
    async def cog_unload(self) -> None:
        await self.bot.reactions.close()


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(ReactionRouterCog(bot))
