"""This module contains the SettingsCog class, which is a cog for the bot that allows server admins to configure the bot"""

from __future__ import annotations

from typing import cast, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext.commands import Context, Cog, hybrid_group, guild_only
from postgrest.types import ReturnMethod

from bot.utils import check_is_staff
from database import DatabaseManager
from database.server_settings import (
    update_server_setting,
    get_server_setting,
    get_server_settings,
)
import bot.utils as utils
from database.schema import ChannelPurpose, CHANNEL_PURPOSES
from bot._types import GuildMessageable

if TYPE_CHECKING:
    from bot.main import RedstoneSquid

# TODO: Make all commands in this cog guild only


class SettingsCog(Cog, name="Settings"):
    def __init__(self, bot: RedstoneSquid):
        self.bot = bot

    @hybrid_group(name="settings", invoke_without_command=True)
    @check_is_staff()
    @guild_only()
    async def settings_hybrid_group(self, ctx: Context):
        """Allows you to configure the bot for your server."""
        await ctx.send_help("settings")

    @Cog.listener("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild):
        """When the bot joins a guild, add the guild to the database."""
        await DatabaseManager().table("server_settings").upsert({"server_id": guild.id}).execute()

    @Cog.listener("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild):
        """When the bot leaves a guild, marks the guild as deleted in the database."""
        await (
            DatabaseManager()
            .table("server_settings")
            .update({"server_id": guild.id, "in_server": False}, returning=ReturnMethod.minimal)
            .execute()
        )

    @settings_hybrid_group.command(name="list")
    @check_is_staff()
    async def show_server_settings(self, ctx: Context):
        """Query all settings."""
        assert ctx.guild is not None
        async with utils.RunningMessage(ctx) as sent_message:
            channels = await get_settable_channels(ctx.guild)

            desc = ""
            for channel_type in CHANNEL_PURPOSES:
                desc += f"`{channel_type.lower()} channel`: {channels.get(channel_type, '_Not set_')}\n"

            await sent_message.edit(embed=utils.info_embed(title="Current Settings", description=desc))

    @settings_hybrid_group.command(name="search")
    @app_commands.describe(channel_purpose=", ".join(CHANNEL_PURPOSES))
    @app_commands.rename(channel_purpose="type")
    @check_is_staff()
    async def search_setting(self, ctx: Context[RedstoneSquid], channel_purpose: ChannelPurpose):
        """Show the server's current setting."""
        assert ctx.guild is not None
        unset_em = utils.info_embed(
            f"{channel_purpose} Channel Info",
            "Unset - Use the set command to set a channel.",
        )
        async with utils.RunningMessage(ctx) as sent_message:
            channel_id = await get_server_setting(ctx.guild.id, channel_purpose)
            if not channel_id:
                await sent_message.edit(embed=unset_em)
                return

            result_channel = ctx.bot.get_channel(channel_id)
            if result_channel is None:
                await sent_message.edit(embed=unset_em)
                return

            assert isinstance(result_channel, GuildMessageable)
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
    @app_commands.rename(channel_purpose="type")
    @check_is_staff()
    async def change_setting(
        self,
        ctx: Context,
        channel_purpose: ChannelPurpose,
        channel: GuildMessageable,
    ):
        """Change the server's setting."""
        assert ctx.guild is not None
        success_embed = utils.info_embed("Settings updated", f"{channel_purpose} channel has successfully been set.")
        failure_embed = utils.error_embed("Error", "Could not find that channel.")

        async with utils.RunningMessage(ctx) as sent_message:
            # Verifying channel exists on server
            if ctx.guild.get_channel(channel.id) is None:
                await sent_message.edit(embed=failure_embed)
                return

            # Updating database
            # TODO: Add a check when adding channels to the database to make sure they are GuildMessageable
            await update_server_setting(ctx.guild.id, channel_purpose, channel.id)
            await sent_message.edit(embed=success_embed)

    @settings_hybrid_group.command(name="clear")
    @app_commands.describe(channel_purpose=", ".join(CHANNEL_PURPOSES))
    @app_commands.rename(channel_purpose="type")
    @check_is_staff()
    async def clear_setting(self, ctx: Context, channel_purpose: ChannelPurpose):
        """Set this setting to None."""
        assert ctx.guild is not None
        success_embed = utils.info_embed(
            "Settings updated",
            f"{channel_purpose} channel has successfully been unset.",
        )

        async with utils.RunningMessage(ctx) as sent_message:
            await update_server_setting(ctx.guild.id, channel_purpose, None)
            await sent_message.edit(embed=success_embed)


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


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(SettingsCog(bot))
