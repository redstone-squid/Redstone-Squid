"""Logging for the bot."""

import logging

from discord.ext import commands
from discord.ext.commands import Cog, CommandError, Context

from squid.bot.errors import handle_context_error

logger = logging.getLogger(__name__)


class LoggingCog[BotT: commands.Bot](Cog, command_attrs=dict(hidden=True)):
    """Global listeners for the bot."""

    def __init__(self, bot: BotT):
        self.bot = bot

    async def log(self, message: str) -> None:
        """Write an operational message without adding Discord I/O to the hot path."""
        logger.info("%s", message)

    # https://discordpy.readthedocs.io/en/stable/api.html#discord.on_ready
    # This function is not guaranteed to be the first event called. Likewise, this function is not guaranteed to only be called once.
    # This library implements reconnection logic and thus will end up calling this event whenever a RESUME request fails.
    @Cog.listener("on_ready")
    async def log_on_ready(self):
        """Logs when the bot is ready."""
        assert self.bot.user is not None
        logger.info(
            "Discord gateway ready, logged in as %s",
            self.bot.user,
            extra={
                "squid.discord.bot_id": self.bot.user.id,
                "squid.discord.guild_count": len(self.bot.guilds),
            },
        )

    @Cog.listener("on_command")
    async def log_command_usage(self, ctx: Context[BotT]):
        """Log low-cardinality command usage without stable human identifiers."""
        assert ctx.command is not None
        logger.info(
            "Discord command invoked",
            extra={
                "squid.command.name": ctx.command.qualified_name,
                "squid.guild.id": ctx.guild.id if ctx.guild is not None else None,
                "squid.discord.interaction": ctx.interaction is not None,
            },
        )

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
