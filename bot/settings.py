"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""
from typing import cast

import discord
from discord import app_commands
from discord.ext.commands import Context, Bot, has_any_role, Cog, hybrid_group, guild_only

from Database.server_settings import (
    update_server_setting,
    get_server_setting,
    get_server_settings,
)
import bot.utils as utils
from Database.schema import ChannelPurpose, CHANNEL_PURPOSES
from bot.schema import GuildMessageable

channel_settings_roles = ["Admin", "Moderator"]

# TODO: Make all commands in this cog guild only


class SettingsCog(Cog, name="Settings"):
    def __init__(self, bot: Bot):
        self.bot = bot

    @hybrid_group(name="settings", invoke_without_command=True)
    @has_any_role(*channel_settings_roles)
    @guild_only()
    async def settings_hybrid_group(self, ctx: Context):
        """Allows you to configure the bot for your server."""
        await ctx.send_help("settings")

    @settings_hybrid_group.command()
    @has_any_role(*channel_settings_roles)
    async def query_all(self, ctx: Context):
        """Query all settings."""
        assert ctx.guild is not None
        async with utils.RunningMessage(ctx) as sent_message:
            channels = await get_settable_channels(ctx.guild)

            desc = ""
            for channel_type in CHANNEL_PURPOSES:
                desc += f"`{channel_type.lower()} channel`: {channels.get(channel_type, '_Not set_')}\n"

            await sent_message.edit(embed=utils.info_embed(title="Current Settings", description=desc))

    @settings_hybrid_group.command(name="query")
    @app_commands.describe(channel_purpose=", ".join(CHANNEL_PURPOSES))
    @has_any_role(*channel_settings_roles)
    async def query_channel(self, ctx: Context, channel_purpose: ChannelPurpose):
        """Finds which channel is set for a purpose and sends the results to the user."""
        assert ctx.guild is not None
        async with utils.RunningMessage(ctx) as sent_message:
            result_channel = await get_channel_for(ctx.guild, channel_purpose)

            if result_channel is None:
                em = utils.info_embed(
                    f"{channel_purpose} Channel Info",
                    "Unset - Use the set command to set a channel.",
                )
            else:
                em = utils.info_embed(
                    f"{channel_purpose} Channel Info",
                    f"ID: {result_channel.id} \n Name: {result_channel.name}",
                )
            await sent_message.edit(embed=em)

    @settings_hybrid_group.command(name="set")
    @app_commands.describe(
        channel_purpose=", ".join(CHANNEL_PURPOSES),
        channel="The channel that you want to set to send this record type to.",
    )
    @has_any_role(*channel_settings_roles)
    async def set_channel(
        self,
        ctx: Context,
        channel_purpose: ChannelPurpose,
        channel: GuildMessageable,
    ):
        """Sets the current channel as the channel to post this record type to."""
        assert ctx.guild is not None
        success_embed = utils.info_embed("Settings updated", f"{channel_purpose} channel has successfully been set.")
        failure_embed = utils.error_embed("Error", "Could not find that channel.")

        async with utils.RunningMessage(ctx) as sent_message:
            # Verifying channel exists on server
            if ctx.guild.get_channel(channel.id) is None:
                await sent_message.edit(embed=failure_embed)
                return

            # Updating database
            await update_server_setting(ctx.guild.id, channel_purpose, channel.id)
            await sent_message.edit(embed=success_embed)

    @settings_hybrid_group.command(name="unset")
    @app_commands.describe(channel_purpose=", ".join(CHANNEL_PURPOSES))
    @has_any_role(*channel_settings_roles)
    async def unset_channel(self, ctx: Context, channel_purpose: ChannelPurpose):
        """Unsets the channel to post this record type to."""
        assert ctx.guild is not None
        success_embed = utils.info_embed(
            "Settings updated",
            f"{channel_purpose} channel has successfully been unset.",
        )

        async with utils.RunningMessage(ctx) as sent_message:
            await update_server_setting(ctx.guild.id, channel_purpose, None)
            await sent_message.edit(embed=success_embed)


async def get_channel_for(server: discord.Guild, channel_purpose: ChannelPurpose) -> GuildMessageable | None:
    """Gets the channel for a specific purpose from the server settings table."""
    channel_id = await get_server_setting(server.id, channel_purpose)
    if channel_id:
        return server.get_channel(channel_id)  # pyright: ignore [reportReturnType]


async def get_settable_channels(
    server: discord.Guild,
) -> dict[ChannelPurpose, GuildMessageable | None]:
    """Gets all record channels of a server from the server settings table."""
    settings = await get_server_settings(server.id)
    channels: dict[ChannelPurpose, GuildMessageable | None] = {}
    for record_type in CHANNEL_PURPOSES:
        channel_id = settings.get(record_type)
        if channel_id is None:
            continue
        channels[record_type] = cast(GuildMessageable | None, server.get_channel(channel_id))

    return channels


async def setup(bot: Bot):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
