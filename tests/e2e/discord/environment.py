from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord
import simcord
from discord.ext import commands

import squid_ui.ext.discord as sd
from squid.bot.help_cog import PROJECT_URL


class CogTestBot(commands.Bot):
    source_code_url = PROJECT_URL


@asynccontextmanager
async def cog_environment() -> AsyncIterator[simcord.Env]:
    bot = CogTestBot(
        command_prefix="!", intents=discord.Intents(guilds=True, members=True, messages=True, message_content=True)
    )
    ui = sd.install(bot)
    await bot.load_extension("squid.bot.help_cog")
    await bot.load_extension("squid.bot.feedback_cog")
    async with ui.running(), simcord.run(bot) as env:
        await bot.tree.sync()
        env.create_guild()
        yield env
