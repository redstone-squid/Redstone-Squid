# FIXME: this file name can't be worse bcs dpy has a utils file
from traceback import format_tb
from types import TracebackType
from typing import Tuple

import discord
from discord.ui import View

from Discord.config import OWNER_ID, PRINT_TRACEBACKS

discord_red = 0xF04747
discord_yellow = 0xFAA61A
discord_green = 0x43B581


def error_embed(title: str, description: str):
    return discord.Embed(
        title=title, colour=discord_red, description=":x: " + description
    )


def warning_embed(title: str, description: str):
    return discord.Embed(
        title=":warning: " + title, colour=discord_yellow, description=description
    )


def info_embed(title: str, description: str):
    return discord.Embed(title=title, colour=discord_green, description=description)


def help_embed(title: str, description: str):
    return discord.Embed(title=title, colour=discord_green, description=description)


def parse_dimensions(dim_str: str) -> Tuple[int, int, int | None]:
    """Parses a string representing dimensions. For example, '5x5' or '5x5x5'."""
    inputs = dim_str.split("x")
    if not 2 <= len(inputs) <= 3:
        raise ValueError(
            "Invalid door size. Must be in the format 'width x height' or 'width x height x depth'"
        )

    try:
        dimensions = list(map(int, inputs))
    except ValueError:
        raise ValueError(
            f"Invalid door size. Each dimension must be parsable as an integer, found {inputs}"
        )

    if len(dimensions) == 2:
        return dimensions[0], dimensions[1], None
    elif len(dimensions) == 3:
        return dimensions[0], dimensions[1], dimensions[2]


class RunningMessage:
    """Context manager to show a working message while the bot is working."""

    def __init__(
        self,
        ctx,
        *,
        title: str = "Working",
        description: str = "Getting information...",
        delete_on_exit: bool = False,
    ):
        self.ctx = ctx
        self.title = title
        self.description = description
        self.sent_message = None
        self.delete_on_exit = delete_on_exit

    async def __aenter__(self):
        self.sent_message = await self.ctx.send(
            embed=info_embed(self.title, self.description)
        )
        return self.sent_message

    async def __aexit__(self, exc_type, exc_val, exc_tb: TracebackType):
        # Handle exceptions
        if exc_type is not None:
            description = f"{str(exc_val)}"
            if PRINT_TRACEBACKS:
                description += f'\n\n```{"".join(format_tb(exc_tb))}```'
            await self.sent_message.edit(
                content=f"<@{OWNER_ID}>",
                embed=error_embed(
                    f"An error has occurred: {exc_type.__name__}", description
                ),
            )
            return False

        # Handle normal exit
        if self.delete_on_exit:
            await self.sent_message.delete()
        return False


class ConfirmationView(View):
    def __init__(self, timeout: int = 60):
        super().__init__(timeout=timeout)
        self.value = None

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.value = True
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
