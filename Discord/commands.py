import os
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import Command_Leaf
from Discord.command_branch import Command_Branch
from Discord.permissions import *

import Discord.config as config

import Discord.settings.settings as settings
import Discord.submission.submissions as submissions

# Main Command Branch --------------------------------------------------------------------------------------------
BOT_NAME = 'Redstone Squid'
BOT_VERSION = '1.0'
SOURCE_CODE_URL = 'https://github.com/Kappeh/Redstone-Squid'

COMMANDS = Command_Branch(BOT_NAME + ' v' + BOT_VERSION)

# Invite Link ----------------------------------------------------------------------------------------------------
async def invite_link(client, user_command, message):
    await client.send_message(message.channel, 'https://discordapp.com/oauth2/authorize?client_id=' + str(client.user.id) + '&scope=bot&permissions=8')

COMMANDS.add_command('invite_link', Command_Leaf(invite_link, 'Invite me to your other servers!'))

# Source code ----------------------------------------------------------------------------------------------------
async def source_code(client, user_command, message):
    await client.send_message(message.channel, 'Source code can be found at: {}.'.format(SOURCE_CODE_URL))

COMMANDS.add_command('source_code', Command_Leaf(source_code, 'Link to {}\'s source code.'.format(BOT_NAME)))

# Option ---------------------------------------------------------------------------------------------------------
COMMANDS.add_command('settings', settings.SETTINGS_COMMANDS)

# Submissions ----------------------------------------------------------------------------------------------------
COMMANDS.add_command('submissions', submissions.SUBMISSIONS_COMMANDS)

# Help -----------------------------------------------------------------------------------------------------------
async def help_func(client, user_command, message):
    argv = user_command.split(' ')[1:]
    help_message = COMMANDS.get_help_message(*argv)
    if isinstance(help_message, discord.Embed):
        return help_message
    help_message += '\nUse `{}help <command>` to get more information.\n'.format(config.PREFIX)
    em = discord.Embed(title = 'Help', description = help_message, colour = 0x43B581)
    await client.send_message(message.channel, embed = em)

help_func_params = [
    Param('cmd', 'The command which you need help with.', dtype = 'text', optional = True)
]

COMMANDS.add_command('help', Command_Leaf(help_func, 'Shows help messages.', params = help_func_params))