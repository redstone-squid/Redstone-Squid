"""Shared command groups for submission cogs."""

from typing import TYPE_CHECKING

from discord.ext.commands import Cog, Context, hybrid_group

if TYPE_CHECKING:
    import squid.bot.app


class BuildCommandGroup[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Own the shared build command group."""

    @hybrid_group(name="build")
    async def build_hybrid_group(self, ctx: Context[BotT]) -> None:
        """Submit, view, edit, and review builds."""
        await ctx.send_help("build")
