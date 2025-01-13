"""Main file for the discord bot, includes logging and the main event loop."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import override, TYPE_CHECKING, Callable, ParamSpec, TypeVar

import discord
from discord import User, Message
from discord.ext import commands
from discord.ext.commands import Cog, Bot, Context, CommandError
from dotenv import load_dotenv

from database import DatabaseManager
from database.utils import utcnow
from bot.config import OWNER_ID, BOT_NAME, BOT_VERSION, PREFIX, DEV_MODE, DEV_PREFIX

if TYPE_CHECKING:
    from collections.abc import Iterable, Awaitable
    T = TypeVar("T")
    P = ParamSpec("P")
    MaybeAwaitableFunc = Callable[P, T | Awaitable[T]]


class Listeners(Cog, command_attrs=dict(hidden=True)):
    """Global listeners for the bot."""

    def __init__(self, bot: RedstoneSquid):
        self.bot: RedstoneSquid = bot

        if not self.bot.owner_id:
            raise RuntimeError("Owner ID not set.")
        self.owner: User | None = self.bot.get_user(self.bot.owner_id)

    async def log(self, msg: str, first_log: bool = False, dm_owner: bool = True) -> None:
        """
        Logs a timestamped message to stdout and to the owner of the bot via DM.

        Args:
            msg: the message to log
            first_log: if True, adds a line of dashes before the message
            dm_owner: whether to send the message to the owner of the bot via DM

        Returns:
            None
        """
        timestamp_msg = utcnow() + msg
        if first_log:
            timestamp_msg = f"{"-" * 90}\n{timestamp_msg}"
        if dm_owner and self.owner:
            await self.owner.send(timestamp_msg)
        print(timestamp_msg)

    # https://discordpy.readthedocs.io/en/stable/api.html#discord.on_ready
    # This function is not guaranteed to be the first event called. Likewise, this function is not guaranteed to only be called once.
    # This library implements reconnection logic and thus will end up calling this event whenever a RESUME request fails.
    @Cog.listener("on_ready")
    async def log_on_ready(self):
        """Logs when the bot is ready."""
        assert self.bot.user is not None
        await self.log(
            f"Bot logged in with name: {self.bot.user.name} and id: {self.bot.user.id}.",
            first_log=True,
        )

    @Cog.listener("on_command")
    async def log_command_usage(self, ctx: Context[RedstoneSquid]):
        """Logs command usage to stdout and to the owner of the bot via DM."""
        if ctx.guild is not None:
            log_message = f'{str(ctx.author)} ran: "{ctx.message.content}" in server: {ctx.guild.name}.'
        else:
            log_message = f'{str(ctx.author)} ran: "{ctx.message.content}" in a private message.'

        owner_dmed_bot = (ctx.guild is None) and await ctx.bot.is_owner(ctx.message.author)
        await self.log(log_message, dm_owner=(not owner_dmed_bot))

    @Cog.listener("on_command_error")
    async def log_command_error(self, ctx: Context[RedstoneSquid], exception: CommandError):
        """Global error handler for the bot."""
        command = ctx.command
        if command and command.has_error_handler():
            return

        cog = ctx.cog
        if cog and cog.has_error_handler():
            return

        if isinstance(exception, commands.CommandNotFound):
            return

        await ctx.send(f"An error occurred: {exception}")

        logging.getLogger(__name__).error("Ignoring exception in command %s", command, exc_info=exception)


class RedstoneSquid(Bot):
    def __init__(
        self,
        command_prefix: (Iterable[str] | str | MaybeAwaitableFunc[[RedstoneSquid, Message], Iterable[str] | str]),
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
        await DatabaseManager.setup()
        await self.load_extension("bot.misc_commands")
        await self.load_extension("bot.settings")
        await self.load_extension("bot.submission.submit")
        await self.add_cog(Listeners(self))
        await self.load_extension("bot.help")
        await self.load_extension("jishaku")
        await self.load_extension("bot.verify")
        await self.load_extension("bot.delete_log")


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
    asyncio.run(main())
