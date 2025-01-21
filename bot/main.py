"""Main file for the discord bot, includes logging and the main event loop."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import override, TYPE_CHECKING, Callable, ParamSpec, TypeVar

import discord
from discord import Message
from discord.ext import commands, tasks
from discord.ext.commands import Bot
from dotenv import load_dotenv

from database import DatabaseManager
from bot.config import OWNER_ID, BOT_NAME, BOT_VERSION, PREFIX, DEV_MODE, DEV_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterable, Awaitable

    T = TypeVar("T")
    P = ParamSpec("P")
    MaybeAwaitableFunc = Callable[P, T | Awaitable[T]]


class RedstoneSquid(Bot):
    db: DatabaseManager

    def __init__(
        self,
        command_prefix: Iterable[str] | str | MaybeAwaitableFunc[[RedstoneSquid, Message], Iterable[str] | str],
    ):
        super().__init__(
            command_prefix=command_prefix,
            owner_id=OWNER_ID,
            intents=discord.Intents.all(),
            description=f"{BOT_NAME} v{BOT_VERSION}",
        )
        assert self.owner_id is not None

    @override
    async def setup_hook(self) -> None:
        """Called when the bot is ready to start."""
        self.db = DatabaseManager()
        await self.load_extension("bot.misc_commands")
        await self.load_extension("bot.settings")
        await self.load_extension("bot.submission.submit")
        await self.load_extension("bot.log")
        await self.load_extension("bot.help")
        await self.load_extension("jishaku")
        await self.load_extension("bot.verify")
        await self.load_extension("bot.delete_log")
        await self.load_extension("bot.admin")
        self.call_supabase_to_prevent_deactivation.start()

    @tasks.loop(hours=24)
    async def call_supabase_to_prevent_deactivation(self):
        """Supabase deactivates a database in the free tier if it's not used for 7 days."""
        await self.db.table("builds").select("id").limit(1).execute()


async def main():
    """Main entry point for the bot."""
    prefix = PREFIX if not DEV_MODE else DEV_PREFIX

    async with RedstoneSquid(command_prefix=commands.when_mentioned_or(prefix)) as bot:
        discord.utils.setup_logging()
        load_dotenv()
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise RuntimeError("Specify discord token either with .env file or a BOT_TOKEN environment variable.")
        await bot.start(token)


if __name__ == "__main__":
    if sys.platform == 'win32':  # https://github.com/aio-libs/aiodns/issues/86
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
