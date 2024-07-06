# FIXME: this file name can't be worse bcs dpy has a utils file
import re
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


def parse_dimensions(dim_str: str, *, min_dim: int = 2, max_dim: int = 3) -> list[int | None]:
    """Parses a string representing dimensions. For example, '5x5' or '5x5x5'.

    Args:
        dim_str: The string to parse
        min_dim: The minimum number of dimensions
        max_dim: The maximum number of dimensions

    Returns:
        A list of the dimensions, the length of the list will be padded with None to match `max_dim`.
    """
    inputs = dim_str.split("x")
    if not min_dim <= len(inputs) <= max_dim:
        raise ValueError(
            f"Invalid number of dimensions. Expected {min_dim} to {max_dim} dimensions, found {len(inputs)} in {dim_str}."
        )

    try:
        dimensions = list(map(int, inputs))
    except ValueError:
        raise ValueError(
            f"Invalid input. Each dimension must be parsable as an integer, found {inputs}"
        )

    # Pad with None
    return dimensions + [None] * (max_dim - len(dimensions))


def parse_hallway_dimensions(dim_str: str) -> Tuple[int | None, int | None, int | None]:
    """Parses a string representing the door's <size>, which essentially is the hallway's dimensions.

    Examples:
        "5x5x5" -> (5, 5, 5)
        "5x5" -> (5, 5, None)
        "5 wide" -> (5, None, None)
        "5 high" -> (None, 5, None)
    
    References:
        https://docs.google.com/document/d/1kDNXIvQ8uAMU5qRFXIk6nLxbVliIjcMu1MjHjLJrRH4/edit

    Returns:
        A tuple of the dimensions (width, height)
    """
    try:
        return parse_dimensions(dim_str)  # type: ignore
    except ValueError:
        if match := re.match(r"^(\d+) (wide|high)$", dim_str):
            size, direction = match.groups()
            if direction == "wide":
                return int(size), None, None
            elif direction == "high":
                return None, int(size), None
        else:
            raise ValueError("Invalid hallway size. Must be in the format 'width x height' or '<width> wide' or '<height> high'")

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
