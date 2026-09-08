"""Gateway-event logging and the fallback prefix-command error handler."""

import logging

from discord.ext import commands
from discord.ext.commands import Cog, CommandError, Context

from squid.bot.errors import handle_context_error

logger = logging.getLogger(__name__)


class LoggingCog[BotT: commands.Bot](Cog, command_attrs=dict(hidden=True)):
    def __init__(self, bot: BotT):
        self.bot = bot

    async def log(self, message: str) -> None:
        logger.info("%s", message)

    # on_ready fires again after every failed RESUME, so this line repeats over a process lifetime.
    @Cog.listener("on_ready")
    async def log_on_ready(self):
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
        """Logs command name, guild id and interaction flag only; no user identifiers."""
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
        """Route to `handle_context_error` unless the command or cog has its own handler; `CommandNotFound` is dropped."""
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
    await bot.add_cog(LoggingCog(bot))
