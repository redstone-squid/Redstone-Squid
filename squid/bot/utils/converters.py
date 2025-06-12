"""Custome converters and utilities for discord.py.

Converters are basically preprocessors for command arguments, allowing you to convert strings into more complex types
or validate them before they are passed to the command function.
"""

from __future__ import annotations

import inspect
from types import FrameType
from typing import Any, override

from discord import Interaction, app_commands
from discord._types import ClientT
from discord.app_commands import Choice
from discord.ext import commands
from discord.ext.commands import Context, FlagConverter

from squid.bot.submission.parse import parse_dimensions


def fix_converter_annotations[_FlagConverter: type[FlagConverter]](cls: _FlagConverter) -> _FlagConverter:
    """
    Fixes discord.py being unable to evaluate annotations if `from __future__ import annotations` is used AND the `FlagConverter` is a nested class.

    This works because discord.py uses the globals() and locals() function to evaluate annotations at runtime.
    See https://discord.com/channels/336642139381301249/1328967235523317862 for more information about this.
    """
    previous_frame: FrameType = inspect.currentframe().f_back  # type: ignore
    previous_frame.f_globals[cls.__name__] = cls
    return cls


class DimensionsConverter(commands.Converter[tuple[int | None, int | None, int | None]]):
    """Converts the string "none" to None and then try to parse the argument as a valid dimension."""

    @override
    async def convert(self, ctx: Context[Any], argument: str) -> tuple[int | None, int | None, int | None]:
        if argument == "none":
            return (None, None, None)

        try:
            dims = parse_dimensions(argument)
        except ValueError:
            raise commands.BadArgument("Invalid dimensions")
        else:
            return dims


class ListConverter(commands.Converter[list[str]]):
    """Converts the string "none" to an empty list and then split the argument by commas."""

    @override
    async def convert(self, ctx: Context[Any], argument: str) -> list[str]:
        if argument == "" or argument == "none":
            return []

        lst = argument.split(",")
        return [x.strip() for x in lst]


class NoneStrConverter(commands.Converter[str | None], app_commands.Transformer[commands.Bot]):
    """Converts the string "none" to None."""

    def __init__(self, choices: list[str] | None = None):
        self._choices = choices

    @override
    async def convert(self, ctx: Context[Any], argument: str) -> str | None:
        if argument == "none":
            return None
        return argument

    @override
    async def transform(self, interaction: Interaction[ClientT], value: Any, /) -> Any:
        if value == "none":
            return None
        return value

    @property
    @override
    def choices(self) -> list[Choice[int | float | str]] | None:
        if self._choices is None:
            return None
        return [Choice(name=choice, value=choice) for choice in self._choices]


class GameTickConverter(commands.Converter[int | None], app_commands.Transformer[commands.Bot]):
    """Converts the string "none" to None and then try to parse the argument as a valid time in game ticks.

    Expects input to be a positive integer or "none"."""

    @override
    async def convert(self, ctx: Context[Any], argument: str) -> int | None:
        if argument == "none":
            return None
        try:
            return int(argument)
        except ValueError:
            raise commands.BadArgument("Invalid integer")

    @override
    async def transform(self, interaction: Interaction[ClientT], value: Any, /) -> Any:
        if value == "none":
            return None
        try:
            return int(value)
        except ValueError:
            raise ValueError("Invalid integer")
