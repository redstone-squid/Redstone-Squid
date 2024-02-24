import os
import discord
from discord.ext import commands
from discord.ext.commands import command, Context

import Discord.utils as utils

# Main Command Branch --------------------------------------------------------------------------------------------
BOT_NAME = 'Redstone Squid'
BOT_VERSION = '1.0'
SOURCE_CODE_URL = 'https://github.com/Kappeh/Redstone-Squid'

FORM_LINK = 'https://forms.gle/i9Nf6apGgPGTUohr9'

@command(brief='Invite me to your other servers!')
async def invite_link(ctx: Context):
    await ctx.send(
        f'https://discordapp.com/oauth2/authorize?client_id={str(ctx.bot.user.id)}&scope=bot&permissions=8')


@command(brief=f'Link to {BOT_NAME}\'s source code.')
async def source_code(ctx: Context):
    await ctx.send(f'Source code can be found at: {SOURCE_CODE_URL}.')


@command(brief='Links you to our record submission form.')
async def submit_record(ctx):
    em = discord.Embed(title='Submission form.',
                       description=f'You can submit new records with ease via our google form: {FORM_LINK}',
                       colour=utils.discord_green)
    # TODO: image is not showing up.
    em.set_image(url='https://i.imgur.com/AqYEd1o.png')
    await ctx.send(embed=em)

# @command(name='help', help='Shows help messages.')
# async def help_func(ctx: Context, *, cmd: str = None):
#     help_message = get_help_message(cmd.split(' ') if cmd else None)
#     if isinstance(help_message, discord.Embed):
#         return help_message
#     help_message += f'\nUse `{config.PREFIX}help <command>` to get more information.\n'
#     em = discord.Embed(title='Help', help=help_message, colour=0x43B581)
#     await ctx.send(embed=em)
