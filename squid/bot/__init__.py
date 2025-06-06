"""Main bot module, includes all the commands and listeners for the bot."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Iterable
from logging.handlers import RotatingFileHandler
from typing import Callable, Self, override

import discord
from discord import Message
from discord.ext import commands, tasks
from discord.ext.commands import Bot
from dotenv import load_dotenv

from squid.bot._types import MessageableChannel
from squid.bot.submission.build_handler import BuildHandler
from squid.config import BOT_NAME, BOT_VERSION, DEV_MODE, DEV_PREFIX, OWNER_ID, PREFIX
from squid.db import DatabaseManager
from squid.db.builds import Build, clean_locks

logger = logging.getLogger(__name__)


type MaybeAwaitableFunc[**P, T] = Callable[P, T | Awaitable[T]]


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
        await self.load_extension("squid.bot.misc_commands")
        await self.load_extension("squid.bot.settings")
        await self.load_extension("squid.bot.submission")
        await self.load_extension("squid.bot.log")
        await self.load_extension("squid.bot.help")
        await self.load_extension("squid.bot.voting.vote")
        await self.load_extension("jishaku")
        await self.load_extension("squid.bot.verify")
        await self.load_extension("squid.bot.admin")
        await self.load_extension("squid.bot.give_redstoner")
        await self.load_extension("squid.bot.version_tracking")
        self.call_supabase_to_prevent_deactivation.start()

    @tasks.loop(hours=24)
    async def call_supabase_to_prevent_deactivation(self):
        """Supabase deactivates a database in the free tier if it's not used for 7 days."""
        await self.db.table("builds").select("id").limit(1).execute()

    @tasks.loop(minutes=5)
    async def clean_dangling_build_locks(self):
        """Clean up dangling build locks in case some functions failed to release them."""
        await clean_locks()

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
            logger.debug("Message %s not found in channel %s.", message_id, channel_id)
            await DatabaseManager().message.untrack_message(message_id)
        except discord.Forbidden:
            pass
        return None

    def for_build(self, build: Build) -> BuildHandler[Self]:
        """A helper function to create a BuildHandler with the bot instance."""
        return BuildHandler(self, build)


def setup_logging():
    """Set up logging for the bot process."""
    # Using format from https://discordpy.readthedocs.io/en/latest/logging.html
    dt_fmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", dt_fmt, style="{")

    logging.root.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.root.addHandler(stream_handler)

    discord_logger = logging.getLogger("discord")
    discord_logger.setLevel(logging.INFO)

    if DEV_MODE:
        # dpy emits heartbeat warning whenever you suspend the bot for over 10 seconds, which is annoying if you attach a debugger
        logging.getLogger("discord.gateway").setLevel(logging.ERROR)

    file_handler = RotatingFileHandler(
        filename="discord.log",
        encoding="utf-8",
        maxBytes=32 * 1024 * 1024,  # 32 MiB
        backupCount=5,  # Rotate through 5 files
    )

    file_handler.setFormatter(formatter)
    discord_logger.addHandler(file_handler)


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
    asyncio.run(main(), debug=DEV_MODE)
