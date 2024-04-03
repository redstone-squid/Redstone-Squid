from typing import Optional, Literal

import discord
from discord import app_commands
from discord.ext.commands import Context, Bot, has_any_role, Cog, hybrid_group

from Database.server_settings import update_server_setting, get_server_setting, get_server_settings
import Discord.utils as utils
from config import RECORD_CHANNEL_TYPES, RECORD_CHANNELS

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
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        channels = get_all_record_channels(ctx.guild)

        # TODO: stop hardcoding this
        desc = """
        `smallest channel`: #{}\n
        `fastest channel`: #{}\n
        `smallest observerless channel`: #{}\n
        `fastest observerless channel`: #{}\n
        `first channel`: #{}\n
        """.format(
            channels.get('Smallest', '_Not set_'),
            channels.get('Fastest', '_Not set_'),
            channels.get('Smallest Observerless', '_Not set_'),
            channels.get('Fastest Observerless', '_Not set_'),
            channels.get('First', '_Not set_')
        )

        em = discord.Embed(title='Current Settings', description=desc, colour=utils.discord_green)
        await sent_message.edit(embed=em)

    @settings_hybrid_group.command(name='query')
    @app_commands.describe(channel_purpose=', '.join(RECORD_CHANNELS))
    @has_any_role(*channel_settings_roles)
    async def query_channel(self, ctx: Context, channel_purpose: Literal["Smallest", "Fastest", "First", "Builds"]):
        """Finds which channel is set for a purpose and sends the results to the user."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        result_channel = get_record_channel_for(ctx.guild, channel_purpose)

        if result_channel is None:
            em = utils.info_embed(f'{channel_purpose} Channel Info', 'Unset - Use the set command to set a channel.')
        else:
            em = utils.info_embed(f'{channel_purpose} Channel Info',
                                  f'ID: {result_channel.id} \n Name: {result_channel.name}')
        await sent_message.edit(embed=em)

    @settings_hybrid_group.command(name='set')
    @app_commands.describe(
        channel_purpose=', '.join(RECORD_CHANNELS),
        channel="The channel that you want to set to send this record type to."
    )
    @has_any_role(*channel_settings_roles)
    async def set_channel(self, ctx: Context, channel_purpose: RECORD_CHANNEL_TYPES,
                          channel: discord.TextChannel):
        """Sets the current channel as the channel to post this record type to."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        # Verifying channel exists on server
        if ctx.guild.get_channel(channel.id) is None:
            await sent_message.delete()
            return utils.error_embed('Error', 'Could not find that channel.')

        # Updating database
        update_server_setting(ctx.guild.id, channel_purpose, channel.id)

        # Sending success message
        await sent_message.edit(
            embed=utils.info_embed('SettingsCog updated', f'{channel_purpose} channel has successfully been set.'))

    @settings_hybrid_group.command(name='unset')
    @app_commands.describe(channel_purpose=', '.join(RECORD_CHANNELS))
    @has_any_role(*channel_settings_roles)
    async def unset_channel(self, ctx: Context, channel_purpose: Literal["Smallest", "Fastest", "First", "Builds"]):
        """Unsets the channel to post this record type to."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
        update_server_setting(ctx.guild.id, channel_purpose, None)
        await sent_message.edit(
            embed=utils.info_embed('SettingsCog updated', f'{channel_purpose} channel has successfully been unset.'))


def get_record_channel_for(server: discord.Guild, channel_purpose: RECORD_CHANNEL_TYPES) -> discord.TextChannel | None:
    """Gets the channel for a specific purpose from the server settings table."""
    channel_id = get_server_setting(server.id, channel_purpose)

    if channel_id is None:
        return None

    return server.get_channel(channel_id)


# Gets all channels
def get_all_record_channels(server: discord.Guild) -> dict[str, Optional[discord.TextChannel]]:
    """
    Gets all record channels of a server from the server settings table.

    This includes the following:
    - Smallest
    - Fastest
    - First
    """
    settings = get_server_settings(server.id)

    channels = {}
    for record_type in RECORD_CHANNELS:
        channel_id = settings.get(record_type)
        channels[record_type] = server.get_channel(channel_id)

    return channels
