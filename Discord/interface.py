"""Main file for the discord bot, includes logging and the main event loop."""
import asyncio
import logging
import os
from pathlib import Path

import discord
import configparser
from discord.ext.commands import Cog, Bot, Context, CommandError

import Discord.utils as utils
from Discord.config import *
from Discord.misc_commands import Miscellaneous
from Discord.help import HelpCog
from Discord.settings import SettingsCog
from Discord.submission.submit import SubmissionsCog
from Discord.submission.voting import VotingCog

# Establishing connection with discord
TOKEN = os.environ.get('DISCORD_TOKEN')
if not TOKEN:
    if os.path.isfile('auth.ini'):
        config = configparser.ConfigParser()
        config_file = Path(__file__).parent.parent / 'auth.ini'
        config.read(config_file)
        TOKEN = config.get('discord', 'token')
    else:
        raise Exception('Specify discord token either with an auth.ini or a DISCORD_TOKEN environment variable.')


# Owner of the bot, used for logging, owner_user_object is only used if the bot can see the owner's user object.
# i.e. the owner is in a server with the bot.
log_user: dict = {'owner_name': OWNER, 'owner_user_object': None}


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
    timestamp_msg = utils.get_time() + msg
    print(timestamp_msg)
    if dm_owner and log_user['owner_user_object']:
        if first_log:
            timestamp_msg = '-' * 90 + '\n' + timestamp_msg
        return await log_user['owner_user_object'].send(timestamp_msg)

class Listeners(Cog, command_attrs=dict(hidden=True)):
    """Global listeners for the bot."""
    def __init__(self, bot):
        self.bot = bot

    @Cog.listener()
    async def on_ready(self):
        # Try to get the user object of the owner of the bot, which is used for logging.
        for member in self.bot.get_all_members():
            if str(member) == log_user['owner_name']:
                log_user['owner_user_object'] = member
        await log(f'Bot logged in with name: {self.bot.user.name} and id: {self.bot.user.id}.', first_log=True)

    # Temporary fix
    # TODO: Remove this event after the bot doesn't break when it is in more than one server
    @Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if guild.id != OWNER_SERVER_ID:
            # Send a warning message in the server, and then leave
            await log(f'Bot joined server: {guild.name} with id: {guild.id}.')
            await guild.system_channel.send('I am not supposed to be in this server. Leaving now.')
            await guild.leave()

    @Cog.listener()
    async def on_message(self, message: discord.Message):
        user_command = ''

        if message.content.startswith(PREFIX):
            user_command = message.content.replace(PREFIX, '', 1)
        elif not message.guild:
            user_command = message.content

        if self.bot.user.id != message.author.id and user_command:
            if message.guild:
                log_message = f'{str(message.author)} ran: "{user_command}" in server: {message.guild.name}.'
            else:
                log_message = f'{str(message.author)} ran: "{user_command}" in a private message.'
            owner_dmed_bot = not message.guild and log_user['owner_name'] == str(message.author)
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

        logging.getLogger(__name__).error('Ignoring exception in command %s', command, exc_info=exception)


async def main(prefix=PREFIX):
    # Running the application
    async with Bot(command_prefix=prefix, owner_id=OWNER_ID, intents=discord.Intents.all(), description=f"{BOT_NAME} v{BOT_VERSION}") as bot:
        await bot.add_cog(Miscellaneous(bot))
        await bot.add_cog(SettingsCog(bot))
        await bot.add_cog(SubmissionsCog(bot))
        await bot.add_cog(Listeners(bot))
        await bot.add_cog(HelpCog(bot))
        await bot.add_cog(VotingCog(bot))
        discord.utils.setup_logging()
        await bot.start(TOKEN)

if __name__ == '__main__':
    asyncio.run(main())
