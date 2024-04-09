from typing import Optional

import discord
from discord import app_commands
from discord.ext.commands import Context, Bot, has_any_role, Cog, hybrid_group

from Database.server_settings import update_server_setting, get_server_setting, get_server_settings
import Discord.utils as utils
from Discord.config import SETTABLE_CHANNELS, SETTABLE_CHANNELS_TYPE

channel_settings_roles = ['Admin', 'Moderator']

class SettingsCog(Cog, name="Settings"):
    def __init__(self, bot: Bot):
        self.bot = bot
    
    @hybrid_group(name="settings", invoke_without_command=True)
    @has_any_role(*channel_settings_roles)
    async def settings_hybrid_group(self, ctx: Context):
        """Allows you to configure the bot for your server."""
        await ctx.send_help("settings")
    
    @settings_hybrid_group.command()
    @has_any_role(*channel_settings_roles)
    async def query_all(self, ctx):
        """Query all settings."""
        async with utils.RunningMessage(ctx) as sent_message:
            channels = await get_settable_channels(ctx.guild)

            desc = ""
            for channel_type in SETTABLE_CHANNELS:
                desc += f"`{channel_type.lower()} channel`: {channels.get(channel_type, '_Not set_')}\n"

            await sent_message.edit(embed=discord.Embed(title='Current Settings', description=desc, colour=utils.discord_green))

    @settings_hybrid_group.command(name='query')
    @app_commands.describe(channel_purpose=', '.join(SETTABLE_CHANNELS))
    @has_any_role(*channel_settings_roles)
    async def query_channel(self, ctx: Context, channel_purpose: SETTABLE_CHANNELS_TYPE):
        """Finds which channel is set for a purpose and sends the results to the user."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        result_channel = await get_channel_for(ctx.guild, channel_purpose)

        if result_channel is None:
            em = utils.info_embed(f'{channel_purpose} Channel Info', 'Unset - Use the set command to set a channel.')
        else:
            em = utils.info_embed(f'{channel_purpose} Channel Info',
                                  f'ID: {result_channel.id} \n Name: {result_channel.name}')
        await sent_message.edit(embed=em)

    @settings_hybrid_group.command(name='set')
    @app_commands.describe(
        channel_purpose=', '.join(SETTABLE_CHANNELS),
        channel="The channel that you want to set to send this record type to."
    )
    @has_any_role(*channel_settings_roles)
    async def set_channel(self, ctx: Context, channel_purpose: SETTABLE_CHANNELS_TYPE,
                          channel: discord.TextChannel):
        """Sets the current channel as the channel to post this record type to."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        # Verifying channel exists on server
        if ctx.guild.get_channel(channel.id) is None:
            await sent_message.edit(embed=utils.error_embed('Error', 'Could not find that channel.'))
            return

        # Updating database
        await update_server_setting(ctx.guild.id, channel_purpose, channel.id)

        # Sending success message
        await sent_message.edit(
            embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been set.'))

    @settings_hybrid_group.command(name='unset')
    @app_commands.describe(channel_purpose=', '.join(SETTABLE_CHANNELS))
    @has_any_role(*channel_settings_roles)
    async def unset_channel(self, ctx: Context, channel_purpose: SETTABLE_CHANNELS_TYPE):
        """Unsets the channel to post this record type to."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
        await update_server_setting(ctx.guild.id, channel_purpose, None)
        await sent_message.edit(
            embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been unset.'))


async def get_channel_for(server: discord.Guild, channel_purpose: SETTABLE_CHANNELS_TYPE) -> discord.TextChannel | None:
    """Gets the channel for a specific purpose from the server settings table."""
    channel_id = await get_server_setting(server.id, channel_purpose)
    return server.get_channel(channel_id)


async def get_settable_channels(server: discord.Guild) -> dict[str, Optional[discord.TextChannel]]:
    """Gets all record channels of a server from the server settings table."""
    settings = await get_server_settings(server.id)

    channels = {}
    for record_type in SETTABLE_CHANNELS:
        channel_id = settings.get(record_type)
        channels[record_type] = server.get_channel(channel_id)

    return channels
