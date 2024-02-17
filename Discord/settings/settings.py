import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import Command_Leaf
from Discord.command_branch import Command_Branch
from Discord.permissions import *

import Database.server_settings as server_settings

# Settings Command Branch ------------------------------------------------------------------------------------------
SETTINGS_COMMANDS = Command_Branch('Allows you to configure the bot for your server.')

# Confirm Channel --------------------------------------------------------------------------------------------
channel_settings_roles = ['Admin', 'Moderator']
channel_set_params = [
    Param('channel', 'The channel that you want to update this setting to.', dtype = 'channel_mention', optional = False)
]

# Returns the channel which has been set to deal with the channel perpose.
def get_channel_for(server, channel_perpose):
    # Getting channel if from database
    channel_id = server_settings.get_server_setting(server.id, channel_perpose)

    # If channel is none or cannot be converted to int, return None.
    if channel_id is None:
        return None
    channel_id = str(channel_id)

    return server.get_channel(channel_id)

# Gets all channels
def get_all_channels(server: discord.Guild):
    """
    Gets all record channels of a server from the server settings table.

    This includes the following:
    - Smallest
    - Fastest
    - Smallest Observerless
    - Fastest Observerless
    - First
    :param server: The server to get the channels from.
    :return: A dictionary with the channel names as keys and the channel objects as values.
    """
    settings = server_settings.get_server_settings(server.id)
    result = {'Smallest': server.get_channel(settings['Smallest']),
              'Fastest': server.get_channel(settings['Fastest']),
              'Smallest Observerless': server.get_channel(settings['Smallest Observerless']),
              'Fastest Observerless': server.get_channel(settings['Fastest Observerless']),
              'First': server.get_channel(settings['First'])}

    return result

# Query all settings.
async def query_all(client, user_command, message):
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Getting information...'))
    
    channels = get_all_channels(message.guild)

    desc = ''
    desc += '`smallest channel`: {}\n'.format('_Not set_' if channels['Smallest'] is None else '#' + channels['Smallest'].name)
    desc += '`fastest channel`: {}\n'.format('_Not set_' if channels['Fastest'] is None else '#' + channels['Fastest'].name)
    desc += '`smallest observerless channel`: {}\n'.format('_Not set_' if channels['Smallest Observerless'] is None else '#' + channels['Smallest Observerless'].name)
    desc += '`fastest observerless channel`: {}\n'.format('_Not set_' if channels['Fastest Observerless'] is None else '#' + channels['Fastest Observerless'].name)
    desc += '`first channel`: {}\n'.format('_Not set_' if channels['First'] is None else '#' + channels['First'].name)

    em = discord.Embed(title='Current Settings', description=desc, colour=utils.discord_green)

    await sent_message.delete()
    await message.channel.send(embed=em)

SETTINGS_COMMANDS.add_command('query_all', Command_Leaf(query_all, 'Queries all settings.', roles = channel_settings_roles))

# Finds which channel is set for a perpose and sends the results to the user.
async def query_channel(client, user_command, message, channel_perpose):
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Getting information...'))
    result_channel = get_channel_for(message.guild, channel_perpose)
    await message.channel.delete(sent_message)
    if result_channel is None:
        return utils.info_embed('{} Channel Info'.format(channel_perpose), 'Unset - Use the set command to set a channel.')
    return utils.info_embed('{} Channel Info'.format(channel_perpose), 'ID: {} \n Name: {}'.format(result_channel.id, result_channel.name))

# Sets the current channel for a perpose.
async def set_channel(client, user_command, message, channel_perpose):
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Updating information...'))

    # channel_id looks like <#599268156258648073>
    # This line removes the <#> from the string.
    # TODO: ugly code
    channel_id = user_command.split(' ')[3]
    for c in '<#>':
        channel_id = str.replace(channel_id, c, '')

    # Verifying channel exists on server
    if message.guild.get_channel(int(channel_id)) is None:
        await sent_message.delete()
        return utils.error_embed('Error', 'Could not find that channel.')

    # Updating database
    server_settings.update_server_setting(message.guild.id, channel_perpose, channel_id)

    # Sending success message
    await sent_message.delete()
    await message.channel.send(embed=utils.info_embed('Settings updated', '{} channel has successfully been set.'.format(channel_perpose)))

# Unsets all channels from having a perpose.
async def unset_channel(client, user_command, message, channel_perpose):
    sent_message = await message.channel.send(embed=utils.info_embed('Working', 'Updating information...'))
    server_id = message.guild.id
    server_settings.update_server_setting(server_id, channel_perpose, '')
    await sent_message.delete()
    await message.channel.send(embed=utils.info_embed('Settings updated', '{} channel has successfully been unset.'.format(channel_perpose)))

# Smallest ---------------------------------------------------------------------------------------------------
# Creating command branch
SMALLEST_CHANNEL_COMMANDS = Command_Branch('Settings for channel to post smallest records to.')
# Defining the query, set and unset functions
async def query_smallest_channel(client, user_command, message):
    return await query_channel(client, user_command, message, 'Smallest')
async def set_smallest_channel(client, user_command, message):
    return await set_channel(client, user_command, message, 'Smallest')
async def unset_smallest_channel(client, user_command, message):
    return await unset_channel(client, user_command, message, 'Smallest')
# Adding functions to the command branch
SMALLEST_CHANNEL_COMMANDS.add_command('query', Command_Leaf(query_smallest_channel, 'Querys which channel is set to post smallest records to.', roles = channel_settings_roles))
SMALLEST_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_smallest_channel, 'Sets current channel as the channel to post smallest records to.', roles = channel_settings_roles, params = channel_set_params))
SMALLEST_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_smallest_channel, 'Unsets the channel to post smallest records to.', roles = channel_settings_roles))
# Adding command branch to the settings command branch
SETTINGS_COMMANDS.add_command('smallest_channel', SMALLEST_CHANNEL_COMMANDS)

# Fastest ----------------------------------------------------------------------------------------------------
# Creating command branch
FASTEST_CHANNEL_COMMANDS = Command_Branch('Settings for channel to post fastest records to.')
# Defining the query, set and unset functions
async def query_fastest_channel(client, user_command, message):
    return await query_channel(client, user_command, message, 'Fastest')
async def set_fastest_channel(client, user_command, message):
    return await set_channel(client, user_command, message, 'Fastest')
async def unset_fastest_channel(client, user_command, message):
    return await unset_channel(client, user_command, message, 'Fastest')
# Adding functions to the command branch
FASTEST_CHANNEL_COMMANDS.add_command('query', Command_Leaf(query_fastest_channel, 'Querys which channel is set to post fastest records to.', roles = channel_settings_roles))
FASTEST_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_fastest_channel, 'Sets current channel as the channel to post fastest records to.', roles = channel_settings_roles, params = channel_set_params))
FASTEST_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_fastest_channel, 'Unsets the channel to post fastest records to.', roles = channel_settings_roles))
# Adding command branch to the settings command branch
SETTINGS_COMMANDS.add_command('fastest_channel', FASTEST_CHANNEL_COMMANDS)

# Smallest Observerless --------------------------------------------------------------------------------------
# Creating command branch
SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS = Command_Branch('Settings for channel to post smallest observerless records to.')
# Defining the query, set and unset functions
async def query_smallest_observerless_channel(client, user_command, message):
    return await query_channel(client, user_command, message, 'Smallest Observerless')
async def set_smallest_observerless_channel(client, user_command, message):
    return await set_channel(client, user_command, message, 'Smallest Observerless')
async def unset_smallest_observerless_channel(client, user_command, message):
    return await unset_channel(client, user_command, message, 'Smallest Observerless')
# Adding functions to the command branch
SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('query', Command_Leaf(query_smallest_observerless_channel, 'Querys which channel is set to post smallest observerless records to.', roles = channel_settings_roles))
SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_smallest_observerless_channel, 'Sets current channel as the channel to post smallest observerless records to.', roles = channel_settings_roles, params = channel_set_params))
SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_smallest_observerless_channel, 'Unsets the channel to post smallest observerless records to.', roles = channel_settings_roles))
# Adding command branch to the settings command branch
SETTINGS_COMMANDS.add_command('smallest_observerless_channel', SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS)

# Fastest Observerless ---------------------------------------------------------------------------------------
# Creating command branch
FASTEST_OBSERVERLESS_CHANNEL_COMMANDS = Command_Branch('Settings for channel to post fastest records to.')
# Defining the query, set and unset functions
async def query_fastest_observerless_channel(client, user_command, message):
    return await query_channel(client, user_command, message, 'Fastest Observerless')
async def set_fastest_observerless_channel(client, user_command, message):
    return await set_channel(client, user_command, message, 'Fastest Observerless')
async def unset_fastest_observerless_channel(client, user_command, message):
    return await unset_channel(client, user_command, message, 'Fastest Observerless')
# Adding functions to the command branch
FASTEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('query', Command_Leaf(query_fastest_observerless_channel, 'Querys which channel is set to post fastest observerless records to.', roles = channel_settings_roles))
FASTEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_fastest_observerless_channel, 'Sets current channel as the channel to post fastest observerless records to.', roles = channel_settings_roles, params = channel_set_params))
FASTEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_fastest_observerless_channel, 'Unsets the channel to post fastest observerless records to.', roles = channel_settings_roles))
# Adding command branch to the settings command branch
SETTINGS_COMMANDS.add_command('fastest_observerless_channel', FASTEST_OBSERVERLESS_CHANNEL_COMMANDS)

# First ------------------------------------------------------------------------------------------------------
# Creating command branch
FIRST_CHANNEL_COMMANDS = Command_Branch('Settings for channel to post first records to.')
# Defining the query, set and unset functions
async def query_first_channel(client, user_command, message):
    return await query_channel(client, user_command, message, 'First')
async def set_first_channel(client, user_command, message):
    return await set_channel(client, user_command, message, 'First')
async def unset_first_channel(client, user_command, message):
    return await unset_channel(client, user_command, message, 'First')
# Adding functions to the command branch
FIRST_CHANNEL_COMMANDS.add_command('query', Command_Leaf(query_first_channel, 'Querys which channel is set to post first records to.', roles = channel_settings_roles))
FIRST_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_first_channel, 'Sets current channel as the channel to post first records to.', roles = channel_settings_roles, params = channel_set_params))
FIRST_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_first_channel, 'Unsets the channel to post first records to.', roles = channel_settings_roles))
# Adding command branch to the settings command branch
SETTINGS_COMMANDS.add_command('first_channel', FIRST_CHANNEL_COMMANDS)