from typing import Optional

import discord
from discord.ext import commands

import Discord.utils as utils
from Discord.command import Param

import Database.server_settings as server_settings

# Confirm Channel --------------------------------------------------------------------------------------------
channel_settings_roles = ['Admin', 'Moderator']
channel_set_params = [
    Param('channel', 'The channel that you want to update this setting to.', dtype = 'channel_mention', optional = False)
]

class Settings(commands.GroupCog, name='settings'):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(description='Queries all settings.')
    @commands.has_any_role(*channel_settings_roles)
    async def query_all(self, ctx):
        """
        Query all settings.

        Args:
            ctx: The context of the command.

        Returns:
            None
        """
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))

        channels = get_all_record_channels(ctx.guild)

        desc = """
        `smallest channel`: {}\n
        `fastest channel`: {}\n
        `smallest observerless channel`: {}\n
        `fastest observerless channel`: {}\n
        `first channel`: {}\n
        """.format(
            '_Not set_' if channels['Smallest'] is None else '#' + channels['Smallest'].name,
            '_Not set_' if channels['Fastest'] is None else '#' + channels['Fastest'].name,
            '_Not set_' if channels['Smallest Observerless'] is None else '#' + channels['Smallest Observerless'].name,
            '_Not set_' if channels['Fastest Observerless'] is None else '#' + channels['Fastest Observerless'].name,
            '_Not set_' if channels['First'] is None else '#' + channels['First'].name
        )

        em = discord.Embed(title='Current Settings', description=desc, colour=utils.discord_green)

        await sent_message.delete()
        await ctx.send(embed=em)

    @commands.command(name='query', description='Queries which channel is set to post this record type to.')
    @commands.has_any_role(*channel_settings_roles)
    async def query_channel(self, ctx: commands.Context, channel_purpose: str):
        """
        Finds which channel is set for a purpose and sends the results to the user.

        Args:
            ctx: The context of the command.
            channel_purpose: "Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"

        Returns:

        """
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))
        result_channel = get_record_channel_for(ctx.guild, channel_purpose)
        await sent_message.delete()
        if result_channel is None:
            em = utils.info_embed(f'{channel_purpose} Channel Info', 'Unset - Use the set command to set a channel.')
        else:
            em = utils.info_embed(f'{channel_purpose} Channel Info', f'ID: {result_channel.id} \n Name: {result_channel.name}')
        await ctx.send(embed=em)

    @commands.command(name='set', description='Sets the current channel as the channel to post this record type to.')
    @commands.has_any_role(*channel_settings_roles)
    async def set_channel(self, ctx: commands.Context, channel_purpose: str, channel: discord.TextChannel):
        """
        Sets the channel for a specific purpose.

        Args:
            ctx: The context of the command.
            channel_purpose: "Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"
            channel: The channel to set.

        Returns:
            None
        """
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        # Verifying channel exists on server
        if ctx.guild.get_channel(channel.id) is None:
            await sent_message.delete()
            return utils.error_embed('Error', 'Could not find that channel.')

        # Updating database
        server_settings.update_server_setting(ctx.guild.id, channel_purpose, channel.id)

        # Sending success message
        await sent_message.delete()
        await ctx.send(
            embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been set.'))

    @commands.command(name='unset', description='Unsets the channel to post this record type to.')
    @commands.has_any_role(*channel_settings_roles)
    async def unset_channel(self, ctx: commands.Context, channel_purpose: str):
        """
        Unsets the channel for a specific purpose.

        Args:
            ctx: The context of the command.
            channel_purpose: "Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"

        Returns:
            None
        """
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
        server_id = ctx.guild.id
        server_settings.update_server_setting(server_id, channel_purpose, '')
        await sent_message.delete()
        await ctx.send(embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been unset.'))


def get_record_channel_for(server: discord.Guild, channel_purpose: str) -> discord.TextChannel | None:
    """
    Gets the channel for a specific purpose from the server settings table.

    Args:
        server: The server to get the channel from.
        channel_purpose: "Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"

    Returns:
        The channel object if it exists, otherwise None.
    """
    # Getting channel if from database
    channel_id = server_settings.get_server_setting(server.id, channel_purpose)

    # If channel is none or cannot be converted to int, return None.
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
    - Smallest Observerless
    - Fastest Observerless
    - First

    Args:
        server: The server to get the channels from.

    Returns:
        A dictionary with the channel names as keys and the channel objects as values.
    """
    settings = server_settings.get_server_settings(server.id)
    result = {'Smallest': server.get_channel(settings['Smallest']),
              'Fastest': server.get_channel(settings['Fastest']),
              'Smallest Observerless': server.get_channel(settings['Smallest Observerless']),
              'Fastest Observerless': server.get_channel(settings['Fastest Observerless']),
              'First': server.get_channel(settings['First'])}

    return result

