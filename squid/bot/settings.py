"""The guild settings workspace and guild-lifecycle registration."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext.commands import Cog

from squid.bot.settings_view import SettingsCapabilities, SettingsPanel
from squid.bot.utils.permissions import allows, enforce, hide_unless, subject_for_interaction
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import SETTINGS_SERVER_EDIT, SETTINGS_SERVER_VIEW, SETTINGS_VOTING_EDIT

if TYPE_CHECKING:
    import squid.bot.app


class SettingsCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Settings"):
    """Open one capability-aware settings workspace per administrator and guild."""

    def __init__(self, bot: BotT):
        self.bot = bot
        self.settings_service = bot.services.settings

    @app_commands.command(name="settings", description="Configure this server")
    @app_commands.guild_only()
    @hide_unless(manage_guild=True)
    async def settings(self, interaction: discord.Interaction[BotT]) -> None:
        """Open this server's channel, locale, emoji, and vote-weight editor."""
        await enforce(
            interaction,
            SETTINGS_SERVER_VIEW,
            SETTINGS_SERVER_EDIT,
            SETTINGS_VOTING_EDIT,
            mode="any",
        )
        guild = interaction.guild
        assert guild is not None
        subject = await subject_for_interaction(interaction)
        permissions = self.bot.services.permissions
        capabilities = SettingsCapabilities(
            view_server=await permissions.allows(subject, SETTINGS_SERVER_VIEW),
            edit_server=await permissions.allows(subject, SETTINGS_SERVER_EDIT),
            edit_voting=await permissions.allows(subject, SETTINGS_VOTING_EDIT),
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(interaction, node)

        await SettingsPanel(
            settings=self.settings_service,
            votes=self.bot.services.votes,
            guild=guild,
            capabilities=capabilities,
            authorize=authorize,
            owner_guild_id=self.bot.owner_server_id,
        ).show(interaction)

    @Cog.listener("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Register a guild when the bot joins it."""
        await self.settings_service.guild_joined(guild.id)

    @Cog.listener("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Remove a guild registration when the bot leaves it."""
        await self.settings_service.guild_removed(guild.id)


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the settings cog."""
    await bot.add_cog(SettingsCog(bot))
