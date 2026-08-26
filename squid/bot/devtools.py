"""Development-only wiring for the Squid Layouts diagnostics cog."""

from discord.ext.commands import Context

import squid.bot.app
import squid_ui_discord as sd


async def _authorized(ctx: Context[squid.bot.app.RedstoneSquid]) -> bool:
    """Keep runtime internals behind both development mode and bot ownership."""
    return ctx.bot.development_mode and await ctx.bot.is_owner(ctx.author)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Add the package-owned devtools with this process's policy and session registry."""
    await bot.add_cog(
        sd.devtools.DevTools(
            check=_authorized,
            registry=bot.mounts,
            scheduler=getattr(bot, "layout_scheduler", None),
        )
    )
