from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import User
from discord.ext import commands
from discord.ext.commands import Cog, Context, CommandError

from database.utils import utcnow

if TYPE_CHECKING:
    from bot.main import RedstoneSquid


class LoggingCog(Cog, command_attrs=dict(hidden=True)):
    """Global listeners for the bot."""

    def __init__(self, bot: RedstoneSquid):
        self.bot = bot

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
        assert ctx.command is not None
        command = f"{ctx.command.qualified_name}"
        if ctx.args:
            command += f" {" ".join(ctx.args)}"
        if ctx.kwargs:
            command += f" {" ".join(f'{k}:{v}' for k, v in ctx.kwargs.items())}"
        if ctx.guild is not None:
            log_message = f'{str(ctx.author)} ran: "{command}" in server: {ctx.guild.name}.'
        else:
            log_message = f'{str(ctx.author)} ran: "{command}" in a private message.'

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


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(LoggingCog(bot))
