"""Logging for the bot."""

import logging

from discord.ext import commands
from discord.ext.commands import Cog, CommandError, Context

from squid.bot.errors import handle_context_error
from squid.bot.utils.components import no_mentions, text_layout
from squid.core.time import utcnow

logger = logging.getLogger(__name__)


class LoggingCog[BotT: commands.Bot](Cog, command_attrs=dict(hidden=True)):
    """Global listeners for the bot."""

    def __init__(self, bot: BotT):
        self.bot = bot

        if not self.bot.owner_id:
            msg = "Owner ID not set."
            raise RuntimeError(msg)

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
            timestamp_msg = f"{'-' * 90}\n{timestamp_msg}"
        if dm_owner:
            owner = self.bot.get_user(self.bot.owner_id) or await self.bot.fetch_user(self.bot.owner_id)
            await owner.send(view=text_layout(timestamp_msg), allowed_mentions=no_mentions())
        logger.info("%s", timestamp_msg)

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
    async def log_command_usage(self, ctx: Context[BotT]):
        """Logs command usage to stdout and to the owner of the bot via DM."""
        assert ctx.command is not None
        command = f"{ctx.command.qualified_name}"
        if ctx.args:
            # The first two arguments are the cog/bot and the context respectively
            command += f" {' '.join(str(arg) for arg in ctx.args[2:])}"
        if ctx.kwargs:
            command += f" {' '.join(f'{k}:{v}' for k, v in ctx.kwargs.items())}"
        if ctx.guild is not None:
            log_message = f'{ctx.author!s} ran: "{command}" in server: {ctx.guild.name}.'
        else:
            log_message = f'{ctx.author!s} ran: "{command}" in a private message.'

        owner_dmed_bot = (ctx.guild is None) and await ctx.bot.is_owner(ctx.message.author)
        await self.log(log_message, dm_owner=(not owner_dmed_bot))

    @Cog.listener("on_command_error")
    async def log_command_error(self, ctx: Context[BotT], exception: CommandError):
        """Global error handler for the bot."""
        command = ctx.command
        if command and command.has_error_handler():
            return

        cog = ctx.cog
        if cog and cog.has_error_handler():
            return

        if isinstance(exception, commands.CommandNotFound):
            return

        await handle_context_error(ctx, exception)


async def setup(bot: commands.Bot):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(LoggingCog(bot))
