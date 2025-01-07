"""Converters for the bot."""
from typing import cast, Any

import discord
from discord import app_commands, AppCommandOptionType
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands._types import BotT
from typing_extensions import override

from database.schema import SETTINGS, Setting, DbSettingKey
from database.server_settings import get_setting_name


# https://discord.com/channels/336642139381301249/1246450626787803156
class SettingConverter(commands.Converter, app_commands.Transformer):
    @override
    async def convert(self, ctx: Context[BotT], argument: str) -> DbSettingKey:
        if argument not in SETTINGS:
            raise commands.BadArgument(f"Invalid setting: {argument}")
        argument = cast(Setting, argument)
        return get_setting_name(argument)

    @override
    async def transform(self, interaction: discord.Interaction, value: Any) -> DbSettingKey:
        try:
            return await self.convert(await Context.from_interaction(interaction), value)
        except commands.BadArgument:
            raise app_commands.TransformerError(value, AppCommandOptionType.string, self)

    @property
    @override
    def _error_display_name(self) -> str:
        return "Setting"

    @property
    @override
    def choices(self):
        _choices: list[app_commands.Choice[str | int | float]] | None = []
        for setting in SETTINGS:
            _choices.append(app_commands.Choice(name=setting, value=setting))
        return _choices
