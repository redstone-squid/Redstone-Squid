import os
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import Command_Leaf
from Discord.command_branch import Command_Branch
from Discord.permissions import *

import Database.server_settings as server_settings

# Option Command Branch ------------------------------------------------------------------------------------------
OPTION_COMMANDS = Command_Branch('Allows you to configure the bot for your server.')

# Confirm Channel --------------------------------------------------------------------------------------------
channel_settings_roles = ['Admin', 'Moderator']

# Returns the channel which has been set to deal with the channel perpose.
def get_channel_for(server, channel_perpose):
    channel_id = server_settings.get_server_setting(server.id, channel_perpose)
    if channel_id == None:
        return None
    result = None
    for channel in server.channels:
        if int(channel.id) == int(channel_id):
            result = channel
            break
    return result

# Finds which channel is set for a perpose and sends the results to the user.
async def query_channel(client, user_command, message, channel_perpose):
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Getting information...'))
    result_channel = get_channel_for(message.server, channel_perpose)
    await client.delete_message(sent_message)
    if result_channel == None:
        return utils.info_embed('{} Channel Info'.format(channel_perpose), 'Unset - Use the set command to set a channel.')
    return utils.info_embed('{} Channel Info'.format(channel_perpose), 'ID: {} \n Name: {}'.format(result_channel.id, result_channel.name))

# Sets the current channel for a perpose.
async def set_channel(client, user_command, message, channel_perpose):
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Updating information...'))
    server_id = message.server.id
    channel_id = message.channel.id
    server_settings.update_server_setting(server_id, channel_perpose, channel_id)
    await client.delete_message(sent_message)
    await client.send_message(message.channel, embed = utils.info_embed('Settings updated', '{} channel has successfully been set.'.format(channel_perpose)))

# Unsets all channels from having a perpose.
async def unset_channel(client, user_command, message, channel_perpose):
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Updating information...'))
    server_id = message.server.id
    server_settings.update_server_setting(server_id, channel_perpose, -1)
    await client.delete_message(sent_message)
    await client.send_message(message.channel, embed = utils.info_embed('Settings updated', '{} channel has successfully been unset.'.format(channel_perpose)))

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
SMALLEST_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_smallest_channel, 'Sets current channel as the channel to post smallest records to.', roles = channel_settings_roles))
SMALLEST_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_smallest_channel, 'Unsets the channel to post smallest records to.', roles = channel_settings_roles))
# Adding command branch to the options command branch
OPTION_COMMANDS.add_command('smallest_channel', SMALLEST_CHANNEL_COMMANDS)

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
FASTEST_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_fastest_channel, 'Sets current channel as the channel to post fastest records to.', roles = channel_settings_roles))
FASTEST_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_fastest_channel, 'Unsets the channel to post fastest records to.', roles = channel_settings_roles))
# Adding command branch to the options command branch
OPTION_COMMANDS.add_command('fastest_channel', FASTEST_CHANNEL_COMMANDS)

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
SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_smallest_observerless_channel, 'Sets current channel as the channel to post smallest observerless records to.', roles = channel_settings_roles))
SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_smallest_observerless_channel, 'Unsets the channel to post smallest observerless records to.', roles = channel_settings_roles))
# Adding command branch to the options command branch
OPTION_COMMANDS.add_command('smallest_observerless_channel', SMALLEST_OBSERVERLESS_CHANNEL_COMMANDS)

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
FASTEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_fastest_observerless_channel, 'Sets current channel as the channel to post fastest observerless records to.', roles = channel_settings_roles))
FASTEST_OBSERVERLESS_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_fastest_observerless_channel, 'Unsets the channel to post fastest observerless records to.', roles = channel_settings_roles))
# Adding command branch to the options command branch
OPTION_COMMANDS.add_command('fastest_observerless_channel', FASTEST_OBSERVERLESS_CHANNEL_COMMANDS)

