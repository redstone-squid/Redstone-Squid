"""The guild settings workspace."""

from typing import TYPE_CHECKING, Self

from discord import app_commands

import squid_ui_discord as sd
from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.bot.utils.permissions import actor_for_interaction, allows, current_edit_actor, enforce, hide_unless
from squid.core.actors import Actor
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import SETTINGS_SERVER_EDIT, SETTINGS_SERVER_VIEW, SETTINGS_VOTING_EDIT
from squid_i18n import tr
from squid_ui_discord.i18n import to_locale_str

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
    async def settings(self, request: sd.Request[Self]) -> SettingsPanel:
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
        subject = await actor_for_interaction(request)
        permissions = self.bot.services.permissions
        capabilities = SettingsCapabilities(
            view_server=(await permissions.check(subject, SETTINGS_SERVER_VIEW)).allowed,
            edit_server=(await permissions.check(subject, SETTINGS_SERVER_EDIT)).allowed,
            edit_voting=(await permissions.check(subject, SETTINGS_VOTING_EDIT)).allowed,
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(request, node)

        async def actor() -> Actor:
            return await current_edit_actor(request)

        return SettingsPanel(
            settings=self.settings_service,
            edits=self.bot.services.edits,
            actor=actor,
            guild=guild,
            capabilities=capabilities,
            authorize=authorize,
            owner_guild_id=self.bot.owner_server_id,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the settings cog."""
    await bot.add_cog(SettingsCog(bot))
