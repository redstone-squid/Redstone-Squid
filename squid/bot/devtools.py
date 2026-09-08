"""Development-only wiring for the Squid Layouts diagnostics cog."""

from discord.ext.commands import Context

import squid.bot.app
import squid_ui_discord as sd


async def _authorized(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
    return ctx.bot.development_mode and await ctx.bot.is_owner(ctx.author)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(
        sd.devtools.DevTools(
            check=_authorized,
            manager=bot.sessions,
            scheduler=bot.ui.scheduler,
        )
    )
