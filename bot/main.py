"""Main file for the discord bot, includes logging and the main event loop."""

import asyncio
import logging
import os

import discord
from discord.ext import commands
from discord.ext.commands import Cog, Bot, Context, CommandError
from dotenv import load_dotenv

from Database.database import DatabaseManager
from Database.utils import utcnow
from bot.config import *
from bot.misc_commands import Miscellaneous
from bot.help import HelpCog
from bot.settings import SettingsCog
from bot.submission.submit import SubmissionsCog
from bot.submission.voting import VotingCog

# Owner of the bot, used for logging, owner_user_object is only used if the bot can see the owner's user object.
# i.e. the owner is in a server with the bot.
log_user: dict = {"owner_name": OWNER, "owner_user_object": None}


async def log(msg: str, first_log=False, dm_owner=True) -> None:
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
    print(timestamp_msg)
    if dm_owner and log_user["owner_user_object"]:
        if first_log:
            timestamp_msg = "-" * 90 + "\n" + timestamp_msg
        return await log_user["owner_user_object"].send(timestamp_msg)


class Listeners(Cog, command_attrs=dict(hidden=True)):
    """Global listeners for the bot."""

    def __init__(self, bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        # Try to get the user object of the owner of the bot, which is used for logging.
        for member in self.bot.get_all_members():
            if str(member) == log_user["owner_name"]:
                log_user["owner_user_object"] = member
        await log(
            f"Bot logged in with name: {self.bot.user.name} and id: {self.bot.user.id}.",
            first_log=True,
        )

    # Temporary fix
    # TODO: Remove this event after the bot doesn't break when it is in more than one server
    @Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if guild.id != OWNER_SERVER_ID:
            # Send a warning message in the server, and then leave
            await log(f"Bot joined server: {guild.name} with id: {guild.id}.")
            if guild.system_channel:
                await guild.system_channel.send("I am not supposed to be in this server. Leaving now.")
            await guild.leave()

    @Cog.listener()
    async def on_message(self, message: discord.Message):
        user_command = ""

        if message.content.startswith(PREFIX):
            user_command = message.content.replace(PREFIX, "", 1)
        elif not message.guild:
            user_command = message.content

        if self.bot.user.id != message.author.id and user_command:
            if message.guild:
                log_message = f'{str(message.author)} ran: "{user_command}" in server: {message.guild.name}.'
            else:
                log_message = f'{str(message.author)} ran: "{user_command}" in a private message.'
            owner_dmed_bot = not message.guild and log_user["owner_name"] == str(message.author)
            if owner_dmed_bot:
                await log(log_message, dm_owner=False)
            else:
                await log(log_message)

    @Cog.listener()
    async def on_command_error(self, ctx: Context, exception: CommandError):
        """Global error handler for the bot."""
        command = ctx.command
        if command and command.has_error_handler():
            return

        cog = ctx.cog
        if cog and cog.has_error_handler():
            return

        logging.getLogger(__name__).error("Ignoring exception in command %s", command, exc_info=exception)


class RedstoneSquid(Bot):
    def __init__(self, command_prefix: str):
        super().__init__(
            command_prefix=command_prefix,
            owner_id=OWNER_ID,
            intents=discord.Intents.all(),
            description=f"{BOT_NAME} v{BOT_VERSION}",
        )

    async def setup_hook(self) -> None:
        await DatabaseManager.setup()
        await self.add_cog(Miscellaneous(self))
        await self.add_cog(SettingsCog(self))
        await self.load_extension("bot.submission.submit")
        await self.add_cog(Listeners(self))
        await self.add_cog(HelpCog(self))
        await self.add_cog(VotingCog(self))


async def main():
    prefix = PREFIX if not DEV_MODE else DEV_PREFIX
    # Running the application
    async with RedstoneSquid(prefix) as bot:
        discord.utils.setup_logging()

        load_dotenv()
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise Exception("Specify discord token either with .env file or a BOT_TOKEN environment variable.")
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
