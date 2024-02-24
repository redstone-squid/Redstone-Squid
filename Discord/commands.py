import discord
from discord.ext.commands import command, Context

import Discord.utils as utils
from Discord.config import SOURCE_CODE_URL, BOT_NAME, FORM_LINK


@command()
async def invite_link(ctx: Context):
    """Invite me to your other servers!"""
    await ctx.send(
        f'https://discordapp.com/oauth2/authorize?client_id={str(ctx.bot.user.id)}&scope=bot&permissions=8')


# Docstring can't be an f-string, so we use the help parameter instead.
@command(help=f"Link to {BOT_NAME}'s source code.")
async def source_code(ctx: Context):
    await ctx.send(f'Source code can be found at: {SOURCE_CODE_URL}.')


@command()
async def submit_record(ctx: Context):
    """Links you to our record submission form."""
    em = discord.Embed(title='Submission form.',
                       description=f'You can submit new records with ease via our google form: {FORM_LINK}',
                       colour=utils.discord_green)
    # TODO: image is not showing up.
    em.set_image(url='https://i.imgur.com/AqYEd1o.png')
    await ctx.send(embed=em)
