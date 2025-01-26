"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""

from __future__ import annotations

from typing import cast, TYPE_CHECKING, Annotated

import discord
from discord import app_commands
from discord.ext.commands import Context, Cog, hybrid_group, guild_only, Greedy
from postgrest.types import ReturnMethod

from squid.bot.utils import check_is_staff
import squid.bot.utils as utils
from squid.database.schema import Setting, SETTINGS
from squid.bot._types import GuildMessageable

if TYPE_CHECKING:
    from squid.bot.main import RedstoneSquid


class SettingsCog[BotT: RedstoneSquid](Cog, name="Settings"):
    def __init__(self, bot: BotT):
        self.bot = bot

    @hybrid_group(name="settings", invoke_without_command=True)
    @check_is_staff()
    @guild_only()
    async def settings_hybrid_group(self, ctx: Context[BotT]):
        """Allows you to configure the bot for your server."""
        await ctx.send_help("settings")

    @Cog.listener("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild):
        """When the bot joins a guild, add the guild to the database."""
        await self.bot.db.table("server_settings").upsert({"server_id": guild.id}).execute()

    @Cog.listener("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild):
        """When the bot leaves a guild, marks the guild as deleted in the database."""
        await (
            self.bot.db.table("server_settings")
            .update({"server_id": guild.id, "in_server": False}, returning=ReturnMethod.minimal)
            .execute()
        )

    @settings_hybrid_group.command(name="list")
    @check_is_staff()
    async def show_server_settings(self, ctx: Context[BotT]):
        """Show all settings for this server."""
        assert ctx.guild is not None
        async with utils.RunningMessage(ctx) as sent_message:
            settings = await self.bot.db.server_setting.get_all(ctx.guild.id)
            desc = ""
            for setting, value in settings.items():
                match setting:
                    case "Smallest" | "Fastest" | "First" | "Builds" | "Vote":
                        value = cast(int | None, value)
                        if value is None:
                            desc += f"{setting} channel: _Not set_\n"
                            continue
                        channel = cast(GuildMessageable | None, ctx.guild.get_channel(value))
                        display_value = channel.name if channel is not None else "_Not found_"
                        desc += f"{setting} channel: {display_value}\n"
                    case "Staff" | "Trusted":
                        value = cast(list[int], value)
                        roles = [role for role in ctx.guild.roles if role.id in value]
                        display_value = ", ".join(role.name for role in roles) or "_Not set_"
                        desc += f"{setting} roles: {display_value}\n"
                    case _:  # pyright: ignore[reportUnnecessaryComparison]  # Should not happen, but may happen if the schema is updated and this code is not
                        desc += f"{setting}: {value}\n"

            await sent_message.edit(embed=utils.info_embed(title="Current Settings", description=desc))

    @settings_hybrid_group.command(name="search")
    @app_commands.describe(setting=", ".join(SETTINGS))
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def search_setting(self, ctx: Context[BotT], setting: Setting):
        """Show the server's current setting."""
        assert ctx.guild is not None

        title: str
        description: str
        async with utils.RunningMessage(ctx) as sent_message:
            match setting:
                case "Smallest" | "Fastest" | "First" | "Builds" | "Vote":
                    title = f"{setting} Channel Info"
                    value = await self.bot.db.server_setting.get_single(ctx.guild.id, setting)
                    if value is None:
                        description = "_Not set_"
                    else:
                        channel = ctx.guild.get_channel(value)
                        description = (
                            f"ID: {channel.id} \n Name: {channel.name}" if channel is not None else "_Not found_"
                        )
                case "Staff" | "Trusted":
                    title = f"{setting} Roles Info"
                    value = await self.bot.db.server_setting.get_single(ctx.guild.id, setting)
                    roles = [role for role in ctx.guild.roles if role.id in value]
                    description = ", ".join(role.name for role in roles) or "_Not set_"
                case _:  # pyright: ignore[reportUnnecessaryComparison]  # Should not happen, but may happen if the schema is updated and this code is not
                    title = setting
                    description = str(self.bot.db.server_setting.get_single(ctx.guild.id, setting))

            await sent_message.edit(embed=utils.info_embed(title=title, description=description))

    @settings_hybrid_group.command(name="set")
    @app_commands.describe(
        setting=", ".join(SETTINGS),
        channel="The channel to send this type of message to",
        roles="The roles which will have this permission",
    )
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def change_setting(
        self,
        ctx: Context[BotT],
        setting: Setting,
        channel: GuildMessageable | None = None,
        roles: Annotated[list[discord.Role] | None, Greedy[discord.Role]] = None,
    ):
        """Change the server's setting."""
        assert ctx.guild is not None

        if channel is not None and roles is not None:
            await ctx.send(
                embed=utils.error_embed("Error", "You can only provide a channel or a list of roles, not both.")
            )
            return

        async with utils.RunningMessage(ctx) as sent_message:
            if setting in {"Smallest", "Fastest", "First", "Builds", "Vote"}:
                if channel is None:
                    await sent_message.edit(
                        embed=utils.error_embed("Error", "You must provide a channel for this setting.")
                    )
                    return

                if ctx.guild.get_channel(channel.id) is None:
                    await sent_message.edit(embed=utils.error_embed("Error", "Could not find that channel."))
                    return

                # TODO: Add a check when adding channels to the database to make sure they are GuildMessageable
                await self.bot.db.server_setting.set(ctx.guild.id, setting, channel.id)
                await sent_message.edit(
                    embed=utils.info_embed("Settings updated", f"{setting} channel has successfully been set.")
                )
            elif setting in {"Staff", "Trusted"}:
                if roles is None:
                    await sent_message.edit(
                        embed=utils.error_embed("Error", "You must provide a list of roles for this setting.")
                    )
                    return

                role_ids = [role.id for role in roles]
                if any(role.guild != ctx.guild for role in roles):
                    await sent_message.edit(embed=utils.error_embed("Error", "The roles must be from this server."))
                    return

                await self.bot.db.server_setting.set(ctx.guild.id, setting, role_ids)
                await sent_message.edit(
                    embed=utils.info_embed("Settings updated", f"{setting} roles have successfully been set.")
                )
            else:  # Should not happen, but may happen if the schema is updated and this code is not
                await sent_message.edit(embed=utils.error_embed("Error", "This setting is not supported."))
                assert False

    @settings_hybrid_group.command(name="clear")
    @app_commands.describe(setting=", ".join(SETTINGS))
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def clear_setting(self, ctx: Context[BotT], setting: Setting):
        """Set this setting to None."""
        assert ctx.guild is not None

        async with utils.RunningMessage(ctx) as sent_message:
            await self.bot.db.server_setting.set(ctx.guild.id, setting, None)
            await sent_message.edit(embed=utils.info_embed("Setting updated", f"{setting} has been cleared."))


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
