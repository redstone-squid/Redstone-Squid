from contextlib import asynccontextmanager
from time import gmtime, strftime

import discord
from discord.ext.commands import Context

from Discord.config import OWNER_ID

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


def get_time():
    raw_time = strftime("%Y/%m/%d %H:%M:%S", gmtime())
    return '[' + raw_time + '] '


def error_embed(title, description):
    return discord.Embed(title=title, colour=discord_red, description=':x: ' + description)


def warning_embed(title, description):
    return discord.Embed(title=':warning: ' + title, colour=discord_yellow, description=description)


def info_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


@asynccontextmanager
async def work_in_progress(ctx: Context):
    """Context manager to show a working message while the bot is working."""
    sent_message = await ctx.send(embed=info_embed('Working', 'Getting information...'))
    try:
        yield sent_message
    except Exception as e:
        # TODO: This may leak a lot of information, but is fine for now.
        await sent_message.edit(content=f"{ctx.bot.get_user(OWNER_ID).mention}", embed=error_embed('An error has occurred', str(e)))
        raise e
    finally:
        pass
