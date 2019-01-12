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
CONFIRM_CHANNEL_COMMANDS = Command_Branch('Settings for channel to post confirmed records to.')
confirm_channel_roles = ['Admin', 'Moderator']

# Query
async def query_confirm_channel(client, user_command, message):
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Getting information...'))

    server_id = message.server.id
    channel_id = server_settings.get_server_setting(server_id, 'Confirm Channel ID')
    
    if channel_id == None:
        await client.delete_message(sent_message)
        return utils.info_embed('Current Confirm Channel', 'Unset - Use the set command to set a confirm channel.')

    confirm_channel = None
    for channel in message.server.channels:
        if int(channel.id) == int(channel_id):
            confirm_channel = channel
            break

    if confirm_channel == None:
        await client.delete_message(sent_message)
        return utils.info_embed('Current Confirm Channel', 'Unset - Use the set command to set a confirm channel.')

    await client.delete_message(sent_message)
    return utils.info_embed('Current Confirm Channel', 'ID: {} \n Name: {}'.format(confirm_channel.id, confirm_channel.name))

CONFIRM_CHANNEL_COMMANDS.add_command('query', Command_Leaf(query_confirm_channel, 'Querys which channel is set to post confirmed records to.', roles = confirm_channel_roles))

# Set
async def set_confirm_channel(client, user_command, message):
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Updating information...'))

    server_id = message.server.id
    channel_id = message.channel.id

    server_settings.update_server_setting(server_id, 'Confirm Channel ID', channel_id)

    await client.delete_message(sent_message)
    await client.send_message(message.channel, embed = utils.info_embed('Settings updated', 'Channel has successfully been set as confirm channel.'))


CONFIRM_CHANNEL_COMMANDS.add_command('set', Command_Leaf(set_confirm_channel, 'Sets current channel as channel to post confirmed records to.', roles = confirm_channel_roles))

# Unset
async def unset_confirm_channel(client, user_command, message):
    sent_message = await client.send_message(message.channel, embed = utils.info_embed('Working', 'Updating information...'))

    server_id = message.server.id
    server_settings.update_server_setting(server_id, 'Confirm Channel ID', -1)

    await client.delete_message(sent_message)
    await client.send_message(message.channel, embed = utils.info_embed('Settings updated', 'Confirm channel has successfully been unset.'))

CONFIRM_CHANNEL_COMMANDS.add_command('unset', Command_Leaf(unset_confirm_channel, 'Unsets the confirm channel as the channel to post confirmed records to.', roles = confirm_channel_roles))

# Adding to command branch
OPTION_COMMANDS.add_command('confirm_channel', CONFIRM_CHANNEL_COMMANDS)