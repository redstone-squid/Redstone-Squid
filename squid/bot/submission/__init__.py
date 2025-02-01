"""Functions, UI components, and classes for handling submissions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from squid.bot.submission.edit import BuildEditCog
from squid.bot.submission.search import SearchCog
from squid.bot.submission.submit import BuildSubmitCog
from squid.bot.submission.ui.components import DynamicBuildEditButton

if TYPE_CHECKING:
    import squid.bot


async def setup(bot: squid.bot.RedstoneSquid) -> None:
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(BuildEditCog(bot))
    await bot.add_cog(SearchCog(bot))
    await bot.add_cog(BuildSubmitCog(bot))
