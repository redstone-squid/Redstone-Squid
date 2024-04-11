# FIXME: this file name can't be worse bcs dpy has a utils file
from traceback import format_tb
from types import TracebackType

import discord
from discord import Embed
from discord.ext.commands import Context

from Discord.config import OWNER_ID, PRINT_TRACEBACKS

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581

def error_embed(title, description):
    return discord.Embed(title=title, colour=discord_red, description=':x: ' + description)


def warning_embed(title, description):
    return discord.Embed(title=':warning: ' + title, colour=discord_yellow, description=description)


def info_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title, description):
    return discord.Embed(title=title, colour=discord_green, description=description)


class RunningMessage:
    """Context manager to show a working message while the bot is working."""
    def __init__(self, ctx: Context, *,
                 title: str = "Working",
                 description: str = "Getting information...",
                 delete_on_exit: bool = False):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.sent_message = None
        self.delete_on_exit = delete_on_exit

    async def __aenter__(self):
        self.sent_message = await self.ctx.send(embed=info_embed(self.title, self.description))
        return self.sent_message

    async def __aexit__(self, exc_type, exc_val, exc_tb: TracebackType):
        # Handle exceptions
        if exc_type is not None:
            description = f'{str(exc_val)}'
            if PRINT_TRACEBACKS:
                description += f'\n\n```{"".join(format_tb(exc_tb))}```'
            await self.sent_message.edit(content=f"{self.ctx.bot.get_user(OWNER_ID).mention}",
                                         embed=error_embed(f'An error has occurred: {exc_type.__name__}',
                                                           description))
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False
