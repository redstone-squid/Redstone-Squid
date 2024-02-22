import os
import discord

import Discord.utils as utils
from Discord.command import Param
from Discord.command_leaf import CommandLeaf
from Discord.command_branch import CommandBranch
from Discord.permissions import *

import Discord.config as config

import Discord.settings.settings as settings
import Discord.submission.submissions as submissions

# Main Command Branch --------------------------------------------------------------------------------------------
BOT_NAME = 'Redstone Squid'
BOT_VERSION = '1.0'
SOURCE_CODE_URL = 'https://github.com/Kappeh/Redstone-Squid'

FORM_LINK = 'https://forms.gle/i9Nf6apGgPGTUohr9'

COMMANDS = CommandBranch(f"{BOT_NAME} v{BOT_VERSION}")


# Invite Link ----------------------------------------------------------------------------------------------------
async def invite_link(client, user_command, message):
    await message.channel.send(
        f'https://discordapp.com/oauth2/authorize?client_id={str(client.user.id)}&scope=bot&permissions=8')


COMMANDS.add_command('invite_link', CommandLeaf(invite_link, 'Invite me to your other servers!'))


# Source code ----------------------------------------------------------------------------------------------------
async def source_code(client, user_command, message):
    await message.channel.send(f'Source code can be found at: {SOURCE_CODE_URL}.')


COMMANDS.add_command('source_code', CommandLeaf(source_code, f'Link to {BOT_NAME}\'s source code.'))


# Submit record --------------------------------------------------------------------------------------------------
async def submit_record(client, user_command, message):
    em = discord.Embed(title='Submission form.',
                       description=f'You can submit new records with ease via our google form: {FORM_LINK}',
                       colour=utils.discord_green)
    # TODO: image is not showing up.
    em.set_image(url='https://i.imgur.com/AqYEd1o.png')
    await message.channel.send(embed=em)


COMMANDS.add_command('submit_record', CommandLeaf(submit_record, 'Links you to our record submission form.'))

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
    help_message += f'\nUse `{config.PREFIX}help <command>` to get more information.\n'
    em = discord.Embed(title='Help', description=help_message, colour=0x43B581)
    await message.channel.send(embeds=[em])


help_func_params = [
    Param('cmd', 'The command which you need help with.', dtype='text', optional=True)
]

COMMANDS.add_command('help', CommandLeaf(help_func, 'Shows help messages.', params=help_func_params))
