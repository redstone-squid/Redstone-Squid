from typing import Optional, Literal

import discord
from discord.ext.commands import Context, Bot, has_any_role, Cog, hybrid_group

from Database.server_settings import update_server_setting, get_server_setting, get_server_settings
import Discord.utils as utils

channel_settings_roles = ['Admin', 'Moderator']


# TODO: Add this description to the help command
# channel_set_params = [
#     Param('channel', 'The channel that you want to update this setting to.', dtype='channel_mention', optional=False)
# ]

class Settings(Cog):
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
    @has_any_role(*channel_settings_roles)
    async def query_channel(self, ctx: Context, channel_purpose: Literal["Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"]):
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
    @has_any_role(*channel_settings_roles)
    async def set_channel(self, ctx: Context, channel_purpose: Literal["Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"],
                          channel: discord.TextChannel):
        """Sets the current channel as the channel to post this record type to."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))

        # Verifying channel exists on server
        if ctx.guild.get_channel(channel.id) is None:
            await sent_message.delete()
            return utils.error_embed('Error', 'Could not find that channel.')

        # TODO: refactor this, only the database should be aware of the setting names, do a lookup there instead
        setting_name = f'{channel_purpose.lower().replace(" ", "_")}_channel_id'
        # Updating database
        update_server_setting(ctx.guild.id, setting_name, channel.id)

        # Sending success message
        await sent_message.edit(
            embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been set.'))

    @settings_hybrid_group.command(name='unset')
    @has_any_role(*channel_settings_roles)
    async def unset_channel(self, ctx: Context, channel_purpose: Literal["Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"]):
        """Unsets the channel to post this record type to."""
        sent_message = await ctx.send(embed=utils.info_embed('Working', 'Updating information...'))
        setting_name = f'{channel_purpose.lower().replace(" ", "_")}_channel_id'
        update_server_setting(ctx.guild.id, setting_name, None)
        await sent_message.edit(
            embed=utils.info_embed('Settings updated', f'{channel_purpose} channel has successfully been unset.'))


def get_record_channel_for(server: discord.Guild, channel_purpose: Literal["Smallest", "Fastest", "Smallest Observerless", "Fastest Observerless", "First"]) -> discord.TextChannel | None:
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
    - Smallest Observerless
    - Fastest Observerless
    - First
    """
    settings = get_server_settings(server.id)
    result = {'Smallest': server.get_channel(settings.get('smallest_channel_id')),
              'Fastest': server.get_channel(settings.get('fastest_channel_id')),
              'Smallest Observerless': server.get_channel(settings.get('smallest_observerless_channel_id')),
              'Fastest Observerless': server.get_channel(settings.get('fastest_observerless_channel_id')),
              'First': server.get_channel(settings.get('first_channel_id'))}

    return result
