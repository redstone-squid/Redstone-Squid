from typing import Optional
import discord
from discord.ext.commands import GroupCog, Context, Bot, has_any_role, group

import Discord.utils as utils
import Database.server_settings as server_settings

channel_settings_roles = ['Admin', 'Moderator']
# channel_set_params = [
#     Param('channel', 'The channel that you want to update this setting to.', dtype='channel_mention', optional=False)
# ]

class Settings(GroupCog, name='settings'):
    def __init__(self, bot: Bot):
        self.bot = bot

    @group(invoke_without_command=True)
    async def settings(self, ctx: Context):
        """Shows help messages for the settings commands."""
        await ctx.send("hi")

    @settings.command(brief='Queries all settings.')
    @has_any_role(*channel_settings_roles)
    async def query_all(self, ctx):
        """Query all settings."""
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

    @settings.command(name='query', brief='Queries which channel is set to post this record type to.')
    @has_any_role(*channel_settings_roles)
    async def query_channel(self, ctx: Context, channel_purpose: str):
        """Finds which channel is set for a purpose and sends the results to the user."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Getting information...'))
        result_channel = get_record_channel_for(ctx.guild, channel_purpose)
        await sent_message.delete()
        if result_channel is None:
            em = utils.info_embed(f'{channel_purpose} Channel Info', 'Unset - Use the set command to set a channel.')
        else:
            em = utils.info_embed(f'{channel_purpose} Channel Info', f'ID: {result_channel.id} \n Name: {result_channel.name}')
        await ctx.send(embed=em)

    @settings.command(name='set', brief='Sets the current channel as the channel to post this record type to.')
    @has_any_role(*channel_settings_roles)
    async def set_channel(self, ctx: Context, channel_purpose: str, channel: discord.TextChannel):
        """Sets the channel for a specific purpose."""
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

    @settings.command(name='unset', brief='Unsets the channel to post this record type to.')
    @has_any_role(*channel_settings_roles)
    async def unset_channel(self, ctx: Context, channel_purpose: str):
        """Unsets the channel for a specific purpose."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
        server_id = ctx.guild.id
        server_settings.update_server_setting(server_id, channel_purpose, '')
        await sent_message.delete()
        await ctx.send(embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been unset.'))


def get_record_channel_for(server: discord.Guild, channel_purpose: str) -> discord.TextChannel | None:
    """Gets the channel for a specific purpose from the server settings table."""
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
    """
    settings = server_settings.get_server_settings(server.id)
    result = {'Smallest': server.get_channel(settings['Smallest']),
              'Fastest': server.get_channel(settings['Fastest']),
              'Smallest Observerless': server.get_channel(settings['Smallest Observerless']),
              'Fastest Observerless': server.get_channel(settings['Fastest Observerless']),
              'First': server.get_channel(settings['First'])}

    return result

