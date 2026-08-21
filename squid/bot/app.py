"""Discord bot application and process entry point."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Self, override

import discord
from discord import Webhook
from discord.abc import Messageable
from discord.ext import commands
from discord.ext.commands import Bot

from squid.bootstrap import create_bot_runtime

# Note that every import to a package that imports back RedstoneSquid (even if it is just in TYPE_CHECKING)
# will create an import cycle from the view of a static type checker, which slows down type checking significantly.
from squid.bot._types import MessageableChannel
from squid.bot.errors import SquidCommandTree, set_error_reporter
from squid.bot.i18n import SquidAppCommandTranslator
from squid.bot.posts import BuildCardRenderer, PostReconciler, StarboardEntryRenderer, VoteSessionRenderer
from squid.bot.reactions import ReactionRouter
from squid.bot.routes import ROUTER
from squid.bot.submission.build_handler import BuildHandler
from squid.bot.utils.embeds import RunningMessage
from squid.bot.utils.permissions import AccountIdCache
from squid.bot.utils.uploads import CatboxClient
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.domain import Build
from squid.config import (
    BotIdentityConfig,
    BotProcessConfig,
    BuildConfig,
    CatboxConfig,
    CommunityConfig,
    NotificationConfig,
    load_bot_process_config,
    load_or_exit,
)
from squid.health import ProcessHealthServer
from squid.logging_config import configure_bot_logging
from squid.observability import configure_observability, correlation_scope
from squid.posts.domain import ResourceKind
from squid.runtime import (
    BackgroundTaskSupervisor,
    BotServices,
    start_log_capture,
    start_permission_epoch_watch,
)

logger = logging.getLogger(__name__)
type MaybeAwaitableFunc[**P, T] = Callable[P, T | Awaitable[T]]
DEFAULT_BOT_IDENTITY = BotIdentityConfig()
DEFAULT_CATBOX_CONFIG = CatboxConfig()
DEFAULT_BUILD_CONFIG = BuildConfig()
DEFAULT_COMMUNITY_CONFIG = CommunityConfig()
DEFAULT_NOTIFICATION_CONFIG = NotificationConfig()
CRITICAL_BOT_JOBS = frozenset({"discord-domain-events", "discord-reconciliation", "notification-deliveries"})

EXTENSIONS = (
    "squid.bot.reactions",
    "squid.bot.messages",
    "squid.bot.misc_commands",
    "squid.bot.layout_showcase",
    "squid.bot.settings",
    "squid.bot.submission",
    "squid.bot.log",
    "squid.bot.help",
    "squid.bot.voting.vote",
    "squid.bot.starboard.cog",
    "squid.bot.sync",
    "squid.bot.events",
    "squid.bot.notifications",
    "squid.bot.verify",
    "squid.bot.admin",
    "squid.bot.diagnostics",
    "squid.bot.permissions",
    "squid.bot.give_redstoner",
    "squid.bot.version_tracking",
    "squid.bot.welcome_relay",
)
"""Every cog the bot loads, in load order.

Module-level rather than local to `setup_hook` so a test can load the real set. A name collision
between two cogs is only raised when they are registered onto the same bot, which no per-cog test
does -- it used to surface as the process failing to start.
"""


class RedstoneSquid(Bot):
    def __init__(
        self,
        services: BotServices,
        config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
        *,
        catbox_config: CatboxConfig = DEFAULT_CATBOX_CONFIG,
        build_config: BuildConfig = DEFAULT_BUILD_CONFIG,
        community_config: CommunityConfig = DEFAULT_COMMUNITY_CONFIG,
        notification_config: NotificationConfig = DEFAULT_NOTIFICATION_CONFIG,
        inference_model: str = "gpt-5.6-luna",
        inference_reasoning_effort: str = "low",
        development_mode: bool = False,
    ):
        self.services = services
        self.build_config = build_config
        self.community_config = community_config
        self.notification_site_url = (
            None
            if notification_config.public_site_url is None
            else str(notification_config.public_site_url).rstrip("/")
        )
        self.inference_model = inference_model
        self.inference_reasoning_effort = inference_reasoning_effort
        self.development_mode = development_mode
        description = f"{config.bot_name} v{config.bot_version}".strip()
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.reactions = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.prefix),
            owner_id=config.owner_id,
            intents=intents,
            description=description or None,
            tree_cls=SquidCommandTree,
        )

        self.bot_name = config.bot_name
        self.bot_version = config.bot_version
        self.owner_server_id = config.owner_server_id
        self.source_code_url = config.source_code_url
        self.background_tasks = BackgroundTaskSupervisor()
        self.reactions = ReactionRouter(self)
        self.post_reconciler = PostReconciler(
            self,
            [BuildCardRenderer(self), VoteSessionRenderer(self), StarboardEntryRenderer(self)],
        )
        self.catbox = CatboxClient(catbox_config)
        self.media_previews = MediaPreviewClient()
        # Permission checks resolve a Discord id to an account on every command,
        # and must never create one, so the lookup is cached rather than avoided.
        self.account_ids = AccountIdCache()

    def is_operational(self) -> bool:
        """Return whether Discord and every critical bot-owned job are healthy."""
        return self.is_ready() and self.background_tasks.is_healthy(CRITICAL_BOT_JOBS, max_age_seconds=60)

    @override
    async def invoke(self, ctx: commands.Context[Any]) -> None:
        """Run a prefix command under one correlation ID, error dispatch included.

        `Bot.invoke` dispatches `on_command_error` itself, so the scope has to wrap it rather than
        the callback alone -- otherwise the handler that presents the error would see no binding
        and mint a second ID unrelated to the log lines the command produced. A hybrid command
        reaching here from the application command tree keeps the ID that tree already bound.
        """
        with correlation_scope():
            await super().invoke(ctx)

    @override
    async def close(self) -> None:
        """Stop gateway-triggered and background work before application resources close."""
        await self.reactions.close()
        await self.background_tasks.close()
        await self.catbox.aclose()
        await self.media_previews.aclose()
        await super().close()

    @override
    async def setup_hook(self) -> None:
        """Called when the bot is ready to start."""
        # Progress-message failures are handled from a `Messageable` that does not expose the
        # client, so the store has to be reachable without one. Registered here rather than in
        # `__init__` so merely constructing a bot in a test does not install a live service.
        set_error_reporter(self.services.error_reports)
        self.background_tasks.capture_failures_into(self.services.error_reports)
        await self.tree.set_translator(SquidAppCommandTranslator())
        # Not a cog: every command's permission check reads through the cache this
        # watcher keeps honest, so it runs before any extension loads rather than
        # as a side effect of one of them being enabled.
        start_permission_epoch_watch(self.background_tasks, self.services.permission_epoch)

        extensions = [*EXTENSIONS]
        if self.development_mode:
            extensions.append("jishaku")

        loaded: list[str] = []
        try:
            for extension in extensions:
                await self.load_extension(extension)
                loaded.append(extension)
        except Exception:
            for extension in reversed(loaded):
                with contextlib.suppress(commands.ExtensionError):
                    await self.unload_extension(extension)
            raise

        # After every extension, because loading is what imports the handler modules that
        # register routes, and installing the router freezes the table.
        ROUTER.register(self)

    async def get_or_fetch_messageable_channel(self, channel_id: int) -> MessageableChannel | None:
        """Resolve a messageable channel from cache or Discord, if it is accessible."""
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.NotFound, discord.Forbidden:
                return None
        if not isinstance(channel, MessageableChannel):
            logger.warning("Channel %s is not messageable.", channel_id)
            return None
        return channel

    async def get_or_fetch_message(self, channel_id: int, message_id: int) -> discord.Message | None:
        """
        Fetches a message from the cache or the API.

        Purely a read. This used to delete the message's tracking row when Discord
        answered 404, which made a lookup quietly destroy state and forced four of its
        eight callers to opt out. Deleted messages are now recorded by the raw delete
        event, and by the reconciler when it finds a post missing.

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

    async def refresh_posts(self, resource_kind: ResourceKind, resource_key: str) -> None:
        """Render a resource's posts now instead of waiting for the reconciler.

        A latency nudge into the same diff loop the background job uses, not a second
        way to publish: the write that prompted it already enqueued durable work, so a
        failure here is retried rather than lost, and a duplicate run is a no-op.
        """
        generation = await self.services.posts.pending_generation(resource_kind, resource_key)
        if generation is None:
            return
        await self.post_reconciler.reconcile(resource_kind, resource_key, generation)


async def main(
    process_config: BotProcessConfig | None = None,
    identity_config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
) -> None:
    """Main entry point for the bot."""
    resolved_config = process_config or load_or_exit(load_bot_process_config)
    queue_listener = configure_bot_logging(resolved_config.logging, dev_mode=resolved_config.development_mode)
    observability = configure_observability(resolved_config.observability, service_name="bot")

    try:
        async with create_bot_runtime(resolved_config.runtime) as runtime:
            bot = RedstoneSquid(
                runtime.services,
                config=identity_config,
                catbox_config=resolved_config.catbox,
                build_config=resolved_config.build,
                community_config=resolved_config.community,
                notification_config=resolved_config.notification,
                inference_model=resolved_config.openai.chat_model,
                inference_reasoning_effort=resolved_config.openai.reasoning_effort,
                development_mode=resolved_config.development_mode,
            )

            async def bot_ready() -> bool:
                await runtime.ready()
                return bot.is_operational()

            # The supervisor's task group has to be entered and exited by the same
            # task, and Bot.close() can be driven from more than one place, so the
            # group is held here and close() only cancels what it owns.
            async with (
                bot.background_tasks.running(),
                bot,
                ProcessHealthServer(bot_ready, port=resolved_config.bot.health_port),
            ):
                start_log_capture(
                    bot.background_tasks,
                    runtime.services.error_reports,
                    enabled=resolved_config.diagnostics.capture_logged_errors,
                    capacity=resolved_config.diagnostics.log_capture_queue,
                )
                await bot.start(resolved_config.discord.token.get_secret_value())
    finally:
        observability.shutdown()
        queue_listener.stop()


if __name__ == "__main__":
    # You probably want to run app.py instead, this is just for convenience
    asyncio.run(main())
