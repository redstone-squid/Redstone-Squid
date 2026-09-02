"""Shared command groups for submission cogs."""

from typing import TYPE_CHECKING

import squid_ui_discord as sd

if TYPE_CHECKING:
    import squid.bot.app


class BuildCommandGroup[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    """Own the app-only build command group."""

    build_group = sd.Group(name="build", description="Browse and submit redstone builds")
