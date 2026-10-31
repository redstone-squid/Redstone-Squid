"""Functions, UI components, and classes for handling submissions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import squid.bot.app


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    from squid.bot.submission.records import RecordCog
    from squid.bot.submission.search import SearchCog
    from squid.bot.submission.ui.components import DynamicBuildEditButton

    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(SearchCog(bot))
    await bot.add_cog(RecordCog(bot))
