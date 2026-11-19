"""The guild settings workspace."""

from typing import TYPE_CHECKING, Self

from discord import app_commands

import squid_ui.ext.discord as sd
from squid.bot.settings.screen import SettingsPanel
from squid.bot.utils.permissions import Grants, enforce, hide_unless
from squid.core.actors import Actor
from squid.core.permissions.domain.catalogue import SETTINGS_SERVER_EDIT, SETTINGS_SERVER_VIEW, SETTINGS_VOTING_EDIT
from squid_i18n import tr
from squid_ui.ext.discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app


class SettingsCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT], name="Settings"):
    """Open one capability-aware settings workspace per administrator and guild."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.settings_service = bot.services.settings

    @sd.command(
        name=to_locale_str(tr(t"command-settings-name = settings")),
        description=to_locale_str(tr(t"command-settings-help = Configure this server")),
    )
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def settings(self, request: sd.Request[Self, BotT]) -> SettingsPanel:
        """Open this server's channel, locale, emoji, and vote-weight editor."""
        await enforce(
            request,
            SETTINGS_SERVER_VIEW,
            SETTINGS_SERVER_EDIT,
            SETTINGS_VOTING_EDIT,
            mode="any",
        )
        guild = request.guild
        assert guild is not None
        grants = await Grants.load(request, SETTINGS_SERVER_VIEW, SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT)

        async def actor() -> Actor:
            return await request.client.actors.resolve(request, refresh=True)

        return SettingsPanel(
            settings=self.settings_service,
            edits=self.bot.services.edits,
            actor=actor,
            guild=guild,
            grants=grants,
            owner_guild_id=self.bot.owner_server_id,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the settings cog."""
    await bot.add_cog(SettingsCog(bot))
