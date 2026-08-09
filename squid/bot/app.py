"""Discord bot application and process entry point."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Self, override

import discord
from discord import Webhook
from discord.abc import Messageable
from discord.ext import commands, tasks
from discord.ext.commands import Bot
from whenever import Instant

from squid.bootstrap import create_application_runtime

# Note that every import to a package that imports back RedstoneSquid (even if it is just in TYPE_CHECKING)
# will create an import cycle from the view of a static type checker, which slows down type checking significantly.
from squid.bot._types import MessageableChannel
from squid.bot.errors import SquidCommandTree
from squid.bot.i18n import SquidAppCommandTranslator
from squid.bot.reactions import ReactionRouter
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.utils.embeds import RunningMessage
from squid.builds.domain import Build
from squid.config import (
    BotIdentityConfig,
    BotProcessConfig,
    BuildConfig,
    CatboxConfig,
    CommunityConfig,
    load_bot_process_config,
)
from squid.logging_config import configure_bot_logging
from squid.observability import configure_observability
from squid.runtime import ApplicationServices

logger = logging.getLogger(__name__)
type MaybeAwaitableFunc[**P, T] = Callable[P, T | Awaitable[T]]
DEFAULT_BOT_IDENTITY = BotIdentityConfig()
DEFAULT_CATBOX_CONFIG = CatboxConfig()
DEFAULT_BUILD_CONFIG = BuildConfig()
DEFAULT_COMMUNITY_CONFIG = CommunityConfig()


class RedstoneSquid(Bot):
    def __init__(
        self,
        services: ApplicationServices,
        keep_database_active: Callable[[], Awaitable[None]],
        config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
        *,
        catbox_config: CatboxConfig = DEFAULT_CATBOX_CONFIG,
        build_config: BuildConfig = DEFAULT_BUILD_CONFIG,
        community_config: CommunityConfig = DEFAULT_COMMUNITY_CONFIG,
        inference_model: str = "gpt-5.6-luna",
        inference_reasoning_effort: str = "low",
    ):
        self.services = services
        self._keep_database_active = keep_database_active
        self.catbox_config = catbox_config
        self.build_config = build_config
        self.community_config = community_config
        self.inference_model = inference_model
        self.inference_reasoning_effort = inference_reasoning_effort
        description = f"{config.bot_name} v{config.bot_version}".strip()
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.prefix),
            owner_id=config.owner_id,
            intents=discord.Intents.all(),
            description=description or None,
            tree_cls=SquidCommandTree,
        )

        self.bot_name = config.bot_name
        self.bot_version = config.bot_version
        self.owner_server_id = config.owner_server_id
        self.source_code_url = config.source_code_url
        self.reactions = ReactionRouter(self)

    @override
    async def setup_hook(self) -> None:
        """Called when the bot is ready to start."""
        await self.tree.set_translator(SquidAppCommandTranslator())

        # Load extensions in parallel to speed up bot startup
        extensions = [
            "squid.bot.reactions",
            "squid.bot.misc_commands",
            "squid.bot.settings",
            "squid.bot.submission",
            "squid.bot.log",
            "squid.bot.help",
            "squid.bot.voting.vote",
            "squid.bot.starboard.cog",
            "squid.bot.sync",
            "squid.bot.events",
            "jishaku",
            "squid.bot.verify",
            "squid.bot.admin",
            "squid.bot.give_redstoner",
            "squid.bot.version_tracking",
            "squid.bot.welcome_relay",
        ]

        await asyncio.gather(*(self.load_extension(ext) for ext in extensions))
        self.keep_database_active.start()

    @tasks.loop(hours=24)
    async def keep_database_active(self):
        """Keep free-tier database hosting active."""
        await self._keep_database_active()

    @tasks.loop(minutes=5)
    async def clean_dangling_build_locks(self):
        """Clean up dangling build locks in case some functions failed to release them."""
        await self.services.builds.clean_stale_locks(older_than=Instant.now().subtract(minutes=5))

    async def get_or_fetch_messageable_channel(self, channel_id: int) -> MessageableChannel | None:
        """Resolve a messageable channel from cache or Discord, if it is accessible."""
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                return None
        if not isinstance(channel, MessageableChannel):
            logger.warning("Channel %s is not messageable.", channel_id)
            return None
        return channel

    async def get_or_fetch_message(
        self, channel_id: int, message_id: int, *, untrack_if_missing: bool = True
    ) -> discord.Message | None:
        """
        Fetches a message from the cache or the API.

        Raises:
            discord.HTTPException: Fetching the channel or message failed.
        """
        channel = await self.get_or_fetch_messageable_channel(channel_id)
        if channel is None:
            return None
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            logger.debug("Message %s not found in channel %s.", message_id, channel_id)
            if untrack_if_missing:
                await self.services.messages.untrack(message_id)
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
        locale: str | None = None,
    ) -> RunningMessage:
        """
        Returns a context manager which can be used to display a message that will be updated
        as the command progresses.

        `title`/`description` are translated into `locale` (resolved via
        `squid.bot.i18n.resolve_locale`) if given, else sent untranslated.

        Usage:
            ```python
            async with bot.get_running_message(ctx, title="Processing") as msg:
                await edit_layout(msg, info_layout("Processing", "Still working..."))
                # Do some work here
                await edit_layout(msg, info_layout("Processing", "Done!"))
            ```
        """
        return RunningMessage(
            ctx,
            title=title,
            description=description,
            delete_on_exit=delete_on_exit,
            locale=locale,
        )

    def for_build(self, build: Build) -> BuildHandler[Self]:
        """A helper function to create a BuildHandler with the bot instance."""
        return BuildHandler(self, build)


async def main(
    process_config: BotProcessConfig | None = None,
    identity_config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
) -> None:
    """Main entry point for the bot."""
    resolved_config = process_config or load_bot_process_config()
    queue_listener = configure_bot_logging(resolved_config.logging, dev_mode=resolved_config.development_mode)
    observability = configure_observability(resolved_config.observability, service_name="bot")

    try:
        async with (
            create_application_runtime(resolved_config.runtime) as runtime,
            RedstoneSquid(
                runtime.services,
                runtime.keep_database_active,
                config=identity_config,
                catbox_config=resolved_config.catbox,
                build_config=resolved_config.build,
                community_config=resolved_config.community,
                inference_model=resolved_config.openai.chat_model,
                inference_reasoning_effort=resolved_config.openai.reasoning_effort,
            ) as bot,
        ):
            await bot.start(resolved_config.discord.token.get_secret_value())
    finally:
        observability.shutdown()
        queue_listener.stop()


if __name__ == "__main__":
    # You probably want to run app.py instead, this is just for convenience
    asyncio.run(main())
