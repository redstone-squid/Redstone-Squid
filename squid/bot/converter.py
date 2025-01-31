from typing import Any, override

from discord.ext import commands
from discord.ext.commands import Context

from squid.bot.submission.parse import parse_dimensions


class DimensionsConverter(commands.Converter):

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


class ListConverter(commands.Converter):

    @override
    async def convert(self, ctx: Context[Any], argument: str) -> list[str]:
        if argument == "" or argument == "none":
            return []

        lst = argument.split(",")
        return [x.strip() for x in lst]

class NoneStrConverter(commands.Converter):

    @override
    async def convert(self, ctx: Context[Any], argument: str) -> str | None:
        if argument == "none":
            return None
        return argument
