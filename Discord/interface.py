import os
import re
import sys
import discord
import configparser

import Discord.utils as utils
from Discord.config import OWNER
from Discord.commands import COMMANDS

# Establishing connection with discord
TOKEN = os.environ.get('DISCORD_TOKEN')
if not TOKEN:
    if os.path.isfile('Discord/auth.ini'):
        config = configparser.ConfigParser()
        config.read('Discord/auth.ini')
        TOKEN = config.get('discord', 'token')
    else:
        raise Exception('Specify discord token either with an auth.ini or a DISCORD_TOKEN environment variable.')

CLIENT = discord.Client()
LOG_USER = {'name': OWNER}

# Log function
def log(msg, first_log = False):
    timestamp_msg = utils.get_time() + msg
    print(timestamp_msg)
    if first_log:
        timestamp_msg = '-' * 90 + '\n' + timestamp_msg
    if 'member' in LOG_USER:
        return CLIENT.send_message(LOG_USER['member'], timestamp_msg)

@CLIENT.event
async def on_ready():
    for member in CLIENT.get_all_members():
        if str(member) == LOG_USER['name']:
            LOG_USER['member'] = member

    await log('Bot logged in with name: {} and id: {}.'.format(CLIENT.user.name, CLIENT.user.id), first_log = True)

# Message event
@CLIENT.event
async def on_message(message):
    user_command = ''
    
    matches = re.findall(r'<@!?' + str(CLIENT.user.id) + '>\s', message.content)

    if len(matches) > 0 and message.content.startswith(matches[0]):
        user_command = message.content.replace(matches[0], '', 1)
    elif not message.server:
        user_command = message.content

    if CLIENT.user.id != message.author.id and user_command:
        log_message = str(message.author) + ' ran: "' + user_command + '"'
        if message.server:
            log_message += ' in server: {}.'.format(message.server.name)
        else:
            log_message += ' in a private message.'
        log_routine = log(log_message)
        if LOG_USER['name'] != str(message.author) or message.server:
            await log_routine

        output = await COMMANDS.execute(user_command, CLIENT, user_command, message)
        if isinstance(output, str):
            await CLIENT.send_message(message.channel, output)
        if isinstance(output, discord.Embed):
            await CLIENT.send_message(message.channel, embed = output)

# Running the application
CLIENT.run(TOKEN)