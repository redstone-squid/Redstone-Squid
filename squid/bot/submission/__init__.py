"""Functions, UI components, and classes for handling submissions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import squid.bot.app


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    from squid.bot.submission.commands import BuildCommands

    await bot.add_cog(BuildCommands(bot))
