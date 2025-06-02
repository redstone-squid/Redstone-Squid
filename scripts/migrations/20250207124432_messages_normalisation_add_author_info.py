"""Complementary migration script for 20250207124432_messages_normalisation

Should be run before the migration to ensure that all messages in the database have their author_id set."""

import asyncio
import os

from discord.ext import commands
from discord.ext.commands.bot import Context
from dotenv import load_dotenv

from squid.bot import RedstoneSquid, setup_logging
from squid.config import DEV_MODE
from squid.db import DatabaseManager


async def migrate(bot: RedstoneSquid):
    db = DatabaseManager()
    response = await db.table("messages").select("*").execute()
    for row in response.data:
        message = await bot.get_or_fetch_message(row["channel_id"], row["message_id"])
        if message is not None:
            await db.table("messages").update({"author_id": message.author.id}).eq("message_id", message.id).execute()


async def main():
    """Main entry point for the bot."""
    setup_logging()
    async with RedstoneSquid(command_prefix=commands.when_mentioned_or(".")) as bot:
        load_dotenv()
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise RuntimeError("Specify discord token either with .env file or a BOT_TOKEN environment variable.")
        await bot.login(token)
        await migrate(bot)


if __name__ == "__main__":
    asyncio.run(main(), debug=DEV_MODE)
