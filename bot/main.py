"""Main file for the discord bot, includes logging and the main event loop."""

from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from typing import override, TYPE_CHECKING, Callable, ParamSpec, TypeVar

import discord
from discord import Message
from discord.ext import commands, tasks
from discord.ext.commands import Bot
from dotenv import load_dotenv

from bot._types import MessageableChannel
from database import DatabaseManager
from bot.config import OWNER_ID, BOT_NAME, BOT_VERSION, PREFIX, DEV_MODE, DEV_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterable, Awaitable

    T = TypeVar("T")
    P = ParamSpec("P")
    MaybeAwaitableFunc = Callable[P, T | Awaitable[T]]


class RedstoneSquid(Bot):
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
        self.db = DatabaseManager(self)
        await self.load_extension("bot.misc_commands")
        await self.load_extension("bot.settings")
        await self.load_extension("bot.submission.submit")
        await self.load_extension("bot.log")
        await self.load_extension("bot.help")
        await self.load_extension("bot.voting.vote")
        await self.load_extension("jishaku")
        await self.load_extension("bot.verify")
        await self.load_extension("bot.admin")
        self.call_supabase_to_prevent_deactivation.start()

    @tasks.loop(hours=24)
    async def call_supabase_to_prevent_deactivation(self):
        """Supabase deactivates a database in the free tier if it's not used for 7 days."""
        await self.db.table("builds").select("id").limit(1).execute()

    async def get_or_fetch_message(self, channel_id: int, message_id: int) -> Message | None:
        """
        Fetches a message from the cache or the API.

        Raises:
            ValueError: The channel is not a MessageableChannel and thus no message can exist in it.
            discord.HTTPException: Fetching the channel or message failed.
            discord.Forbidden: The bot does not have permission to fetch the channel or message.
            discord.NotFound: The channel or message was not found.
        """
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, MessageableChannel):
            raise ValueError("Channel is not a messageable channel.")
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            pass
            # await untrack_message(message_id)  # FIXME: This is accidentally removing a lot of messages
        except discord.Forbidden:
            pass
        return None


def setup_logging():
    """Set up logging for the bot process."""
    # Using format from https://discordpy.readthedocs.io/en/latest/logging.html
    dt_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{")

    logging.root.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.root.addHandler(stream_handler)

    logger = logging.getLogger("discord")
    logger.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        filename="discord.log",
        encoding="utf-8",
        maxBytes=32 * 1024 * 1024,  # 32 MiB
        backupCount=5,  # Rotate through 5 files
    )

    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


async def main():
    """Main entry point for the bot."""
    prefix = PREFIX if not DEV_MODE else DEV_PREFIX

    setup_logging()
    async with RedstoneSquid(command_prefix=commands.when_mentioned_or(prefix)) as bot:
        load_dotenv()
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise RuntimeError("Specify discord token either with .env file or a BOT_TOKEN environment variable.")
        await bot.start(token)


if __name__ == "__main__":
    if sys.platform == "win32":  # https://github.com/aio-libs/aiodns/issues/86
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
