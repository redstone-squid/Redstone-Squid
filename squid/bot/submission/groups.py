"""Shared command groups for submission cogs."""

from typing import TYPE_CHECKING

import squid_ui_discord as sd
from squid_i18n import tr
from squid_ui_discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app


class BuildCommandGroup[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Own the app-only build command group."""

    build_group = sd.Group(
        name=to_locale_str(tr(t"command-build-name = build")),
        description=to_locale_str(tr(t"command-build-help = Browse and submit redstone builds")),
    )
