"""Main bot module, includes all the commands and listeners for the bot."""

import asyncio
import logging
import os
from collections.abc import Awaitable
from logging.handlers import RotatingFileHandler
from typing import Callable, Final, Self, TypedDict, override

import discord
from discord import Webhook
from discord.abc import Messageable
from discord.ext import commands, tasks
from discord.ext.commands import Bot
from dotenv.main import StrPath

# Note that every import to a package that imports back RedstoneSquid (even if it is just in TYPE_CHECKING)
# will create an import cycle from the view of a static type checker, which slows down type checking significantly.
from squid.bot._types import MessageableChannel
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.utils import RunningMessage
from squid.db import DatabaseManager
from squid.db.builds import Build, clean_locks
from squid.db.schema import Base

logger = logging.getLogger(__name__)
type MaybeAwaitableFunc[**P, T] = Callable[P, T | Awaitable[T]]


class BotConfig(TypedDict, total=False):
    """Configuration for the Redstone Squid bot."""

    prefix: str
    """The command prefix for the bot. Defaults to `!` if not found in this config."""
    bot_name: str
    """The name of the bot, used in the help command."""
    bot_version: str
    """The version of the bot, used in the help command."""
    owner_id: int
    """The ID of the bot owner, used for commands that only the owner can use. e.g. `sync` which syncs the bot's commands with Discord."""
    owner_server_id: int
    """Select one "home" server where some commands are only available in this server. If not set, the bot will not restrict commands to a specific server."""
    source_code_url: str
    """The URL of the source code repository, used in the help command."""
    print_tracebacks: bool
    """Whether to print tracebacks directly to the user, may leak system information"""


class ApplicationConfig(TypedDict, total=False):
    """Configuration for the Redstone Squid system."""

    dev_mode: bool
    """Whether the bot is running in development mode, which changes some small behaviors to make development easier."""
    dotenv_path: StrPath | None
    """The path to the .env file, used to load environment variables. Use None for auto-detection, remove this key to disable loading .env file."""
    bot_config: BotConfig
    """Configuration for the bot."""


class RedstoneSquid(Bot):
    def __init__(
        self,
        db: DatabaseManager,
        config: BotConfig | None = None,
    ):
        self.db = db
        if config is None:
            config = {}
        description = ""
        if config.get("bot_name"):
            description += f"{config.get('bot_name')} "
        if config.get("bot_version"):
            description += f"v{config.get('bot_version')}"

        prefix = config.get("prefix")
        if prefix is None:
            logger.info("No prefix found in config, using default '!'")
            prefix = "!"
        super().__init__(
            command_prefix=commands.when_mentioned_or(prefix),
            owner_id=config.get("owner_id"),
            intents=discord.Intents.all(),
            description=description or None,
        )

        # Store bot configuration as instance attributes
        self.bot_name = config.get("bot_name")
        self.bot_version = config.get("bot_version")
        self.owner_server_id = config.get("owner_server_id")
        self.source_code_url = config.get("source_code_url")
        self.print_tracebacks = config.get("print_tracebacks", False)

    @override
    async def setup_hook(self) -> None:
        """Called when the bot is ready to start."""
        # Load extensions in parallel to speed up bot startup
        extensions = [
            "squid.bot.misc_commands",
            "squid.bot.settings",
            "squid.bot.submission",
            "squid.bot.log",
            "squid.bot.help",
            # "squid.bot.voting.vote",  # FIXME: This is clogging the event loop
            "jishaku",
            "squid.bot.verify",
            "squid.bot.admin",
            "squid.bot.give_redstoner",
            "squid.bot.version_tracking",
        ]

        await asyncio.gather(*(self.load_extension(ext) for ext in extensions))
        self.call_supabase_to_prevent_deactivation.start()

    @tasks.loop(hours=24)
    async def call_supabase_to_prevent_deactivation(self):
        """Supabase deactivates a database in the free tier if it's not used for 7 days."""
        await self.db.table("builds").select("id").limit(1).execute()

    @tasks.loop(minutes=5)
    async def clean_dangling_build_locks(self):
        """Clean up dangling build locks in case some functions failed to release them."""
        await clean_locks()

    async def get_or_fetch_message(self, channel_id: int, message_id: int) -> discord.Message | None:
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

    def get_running_message(
        self,
        ctx: Messageable | Webhook,
        *,
        title: str = "Working",
        description: str = "Getting information...",
        delete_on_exit: bool = False,
    ) -> RunningMessage:
        """
        Returns a context manager which can be used to display a message that will be updated
        as the command progresses.

        Usage:
            ```python
            async with bot.get_running_message(ctx, title="Processing") as msg:
                await msg.edit(description="Still working...")
                # Do some work here
                await msg.edit(description="Done!")
            ```
        """
        return RunningMessage(
            ctx,
            title=title,
            description=description,
            delete_on_exit=delete_on_exit,
            print_tracebacks=self.print_tracebacks,
            id_to_mention_on_error=self.owner_id,
        )

    def for_build(self, build: Build) -> BuildHandler[Self]:
        """A helper function to create a BuildHandler with the bot instance."""
        return BuildHandler(self, build)


def setup_logging(dev_mode: bool = False):
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

    if dev_mode:
        # See https://github.com/sqlalchemy/sqlalchemy/discussions/10302
        logging.getLogger("sqlalchemy.engine.Engine").handlers = [logging.NullHandler()]  # Avoid duplicate logging

        # dpy emits heartbeat warning whenever you suspend the bot for over 10 seconds, which is annoying if you attach a debugger
        logging.getLogger("discord.gateway").setLevel(logging.ERROR)

    # Create logs directory if it doesn't exist
    logs_dir = "logs"
    os.makedirs(logs_dir, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=os.path.join(logs_dir, "discord.log"),
        encoding="utf-8",
        maxBytes=32 * 1024 * 1024,  # 32 MiB
        backupCount=5,  # Rotate through 5 files
    )

    file_handler.setFormatter(formatter)
    discord_logger.addHandler(file_handler)


DEFAULT_CONFIG: Final[ApplicationConfig] = {
    "dev_mode": False,
    "bot_config": {
        "prefix": "!",
        "bot_name": "Redstone Squid",
    },
}


async def main(config: ApplicationConfig = DEFAULT_CONFIG):
    """Main entry point for the bot."""
    setup_logging(config.get("dev_mode", False))

    db = DatabaseManager()
    # Run the synchronous db validation function in a thread to avoid blocking the event loop
    asyncio.get_event_loop().run_in_executor(
        None,
        lambda: db.validate_database_consistency(Base),
    ).add_done_callback(lambda future: future.result())  # If the validation fails, it will raise an exception

    async with RedstoneSquid(db, config=config.get("bot_config")) as bot:
        token = os.environ.get("BOT_TOKEN")
        if not token:
            raise RuntimeError("Specify discord token either with .env file or a BOT_TOKEN environment variable.")
        await bot.start(token)


if __name__ == "__main__":
    # You probably want to run app.py instead, this is just for convenience
    asyncio.run(main())
