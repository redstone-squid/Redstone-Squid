import os
import re
import sys
import discord
import discord.ext.commands as commands
import configparser
import asyncio

import Discord.utils as utils
from Discord.config import *
from Discord.commands import COMMANDS, help_func

# Establishing connection with discord
TOKEN = os.environ.get('DISCORD_TOKEN')
if not TOKEN:
    if os.path.isfile('Discord/auth.ini'):
        config = configparser.ConfigParser()
        config.read('Discord/auth.ini')
        TOKEN = config.get('discord', 'token')
    else:
        raise Exception('Specify discord token either with an auth.ini or a DISCORD_TOKEN environment variable.')

bot = commands.Bot('!', owner_id=OWNER_ID, intents=discord.Intents.all())
# Owner of the bot, used for logging, owner_user_object is only used if the bot can see the owner's user object.
# i.e. the owner is in a server with the bot.
log_user: dict = {'owner_name': OWNER, 'owner_user_object': None}


# Log function
async def log(msg: str, first_log=False, dm=True):
    timestamp_msg = utils.get_time() + msg
    print(timestamp_msg)
    if log_user['owner_user_object'] and dm:
        if first_log:
            timestamp_msg = '-' * 90 + '\n' + timestamp_msg
        return await log_user['owner_user_object'].send(timestamp_msg)


@bot.event
async def on_ready():
    # Cryptic code that seems to be trying to get the user object of the owner of the bot,
    # and then sending a message to that user only if the bot can see the owner's user object.
    for member in bot.get_all_members():
        if str(member) == log_user['owner_name']:
            log_user['owner_user_object'] = member
    await log(f'Bot logged in with name: {bot.user.name} and id: {bot.user.id}.', first_log=True)


# Message event
@bot.event
async def on_message(message: discord.Message):
    user_command = ''

    if message.content.startswith(PREFIX):
        user_command = message.content.replace(PREFIX, '', 1)
    elif not message.guild:
        user_command = message.content

    if bot.user.id != message.author.id and user_command:
        if message.guild:
            log_message = f'{str(message.author)} ran: "{user_command}" in server: {message.guild.name}.'
        else:
            log_message = f'{str(message.author)} ran: "{user_command}" in a private message.'
        owner_dmed_bot = not message.guild and log_user['owner_name'] == str(message.author)
        if owner_dmed_bot:
            await log(log_message, dm=False)
        else:
            await log(log_message)

        output = await COMMANDS.execute(user_command, bot, user_command, message)
        if isinstance(output, str):
            await message.channel.send(output)
        if isinstance(output, discord.Embed):
            await message.channel.send(embed=output)


# Running the application
bot.run(TOKEN)
