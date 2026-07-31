"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""

from typing import TYPE_CHECKING, Annotated, cast

import discord
from beartype.door import is_bearable
from discord import app_commands
from discord.ext.commands import Cog, Context, Greedy, guild_only, hybrid_group

from squid.bot._types import GuildMessageable
from squid.bot.utils.components import edit_layout, error_layout, info_layout, no_mentions
from squid.bot.utils.permissions import check_is_staff
from squid.settings.domain import ListRoleSetting, ScalarChannelSetting, Setting

if TYPE_CHECKING:
    import squid.bot.app


class SettingsCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="Settings"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.settings_service = bot.services.settings

    @hybrid_group(name="settings")
    @check_is_staff()
    @guild_only()
    async def settings_hybrid_group(self, ctx: Context[BotT]):
        """Allows you to configure the bot for your server."""
        await ctx.send_help("settings")

    @Cog.listener("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild):
        """Let the db know that the bot has joined a new guild."""
        await self.settings_service.guild_joined(guild.id)

    @Cog.listener("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild):
        """Let the db know that the bot has left a guild."""
        await self.settings_service.guild_removed(guild.id)

    @settings_hybrid_group.command(name="list")
    @check_is_staff()
    async def show_server_settings(self, ctx: Context[BotT]):
        """Show all settings for this server."""
        assert ctx.guild is not None
        async with self.bot.get_running_message(ctx) as sent_message:
            settings = await self.settings_service.get_all(ctx.guild.id)
            desc = ""
            for setting, value in settings.items():
                if is_bearable(setting, ScalarChannelSetting):
                    value = cast(int | None, value)
                    if value is None:
                        desc += f"{setting} channel: _Not set_\n"
                        continue
                    # noinspection PyTypeHints: PyCharm thinks this cast is invalid
                    channel = cast(GuildMessageable | None, ctx.guild.get_channel(value))
                    display_value = channel.name if channel is not None else "_Not found_"
                    desc += f"{setting} channel: {display_value}\n"
                elif is_bearable(setting, ListRoleSetting):
                    value = cast(list[int], value)
                    roles = [role for role in ctx.guild.roles if role.id in value]
                    display_value = ", ".join(role.name for role in roles) or "_Not set_"
                    desc += f"{setting} roles: {display_value}\n"
                else:  # Should not happen, but may happen if the schema is updated and this code is not
                    desc += f"{setting}: {value}\n"

            await edit_layout(
                sent_message,
                info_layout(title="Current Settings", description=desc),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="get")
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def get_setting(self, ctx: Context[BotT], setting: Setting):
        """Show the server's current setting."""
        assert ctx.guild is not None

        title: str
        description: str
        async with self.bot.get_running_message(ctx) as sent_message:
            match setting:
                case "Smallest" | "Fastest" | "First" | "Builds" | "Vote":
                    title = f"{setting} Channel Info"
                    value = await self.settings_service.get(ctx.guild.id, setting)
                    if value is None:
                        description = "_Not set_"
                    else:
                        channel = ctx.guild.get_channel(value)
                        description = (
                            f"ID: {channel.id} \n Name: {channel.name}" if channel is not None else "_Not found_"
                        )
                case "Staff" | "Trusted":
                    title = f"{setting} Roles Info"
                    value = await self.settings_service.get(ctx.guild.id, setting)
                    roles = [role for role in ctx.guild.roles if role.id in value]
                    description = ", ".join(role.name for role in roles) or "_Not set_"
                case _:  # pyright: ignore[reportUnnecessaryComparison]  # Should not happen, but may happen if the schema is updated and this code is not
                    title = setting
                    description = str(await self.settings_service.get(ctx.guild.id, setting))

            await edit_layout(
                sent_message,
                info_layout(title=title, description=description),
                allowed_mentions=no_mentions(),
            )

    @settings_hybrid_group.command(name="set")
    @app_commands.describe(
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
                view=error_layout("Error", "You can only provide a channel or a list of roles, not both."),
                allowed_mentions=no_mentions(),
            )
            return

        async with self.bot.get_running_message(ctx) as sent_message:
            if is_bearable(setting, ScalarChannelSetting):
                if channel is None:
                    await edit_layout(
                        sent_message,
                        error_layout("Error", "You must provide a channel for this setting."),
                        allowed_mentions=no_mentions(),
                    )
                    return

                if ctx.guild.get_channel(channel.id) is None:
                    await edit_layout(
                        sent_message,
                        error_layout("Error", "Could not find that channel."),
                        allowed_mentions=no_mentions(),
                    )
                    return

                # TODO: Add a check when adding channels to the database to make sure they are GuildMessageable
                await self.settings_service.set_channel(ctx.guild.id, setting, channel.id)
                await edit_layout(
                    sent_message,
                    info_layout("Settings updated", f"{setting} channel has successfully been set."),
                    allowed_mentions=no_mentions(),
                )
            elif is_bearable(setting, ListRoleSetting):
                if roles is None:
                    await edit_layout(
                        sent_message,
                        error_layout("Error", "You must provide a list of roles for this setting."),
                        allowed_mentions=no_mentions(),
                    )
                    return

                role_ids = [role.id for role in roles]
                if any(role.guild != ctx.guild for role in roles):
                    await edit_layout(
                        sent_message,
                        error_layout("Error", "The roles must be from this server."),
                        allowed_mentions=no_mentions(),
                    )
                    return

                await self.settings_service.set_roles(ctx.guild.id, setting, role_ids)
                await edit_layout(
                    sent_message,
                    info_layout("Settings updated", f"{setting} roles have successfully been set."),
                    allowed_mentions=no_mentions(),
                )
            else:  # Should not happen, but may happen if the schema is updated and this code is not
                await edit_layout(
                    sent_message,
                    error_layout("Error", "This setting is not supported."),
                    allowed_mentions=no_mentions(),
                )
                raise AssertionError()

    @settings_hybrid_group.command(name="clear")
    @app_commands.rename(setting="type")
    @check_is_staff()
    async def clear_setting(self, ctx: Context[BotT], setting: Setting):
        """Set this setting to None."""
        assert ctx.guild is not None

        async with self.bot.get_running_message(ctx) as sent_message:
            await self.settings_service.clear(ctx.guild.id, setting)
            await edit_layout(
                sent_message,
                info_layout("Setting updated", f"{setting} has been cleared."),
                allowed_mentions=no_mentions(),
            )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
