"""Discord bot application and process entry point."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Self, cast, override

import anyio
import discord
from discord.ext import commands
from discord.ext.commands import Bot
from whenever import Instant

import squid_ui.ext.discord as sd
from squid.bootstrap import create_bot_runtime

# Note that every import to a package that imports back RedstoneSquid (even if it is just in TYPE_CHECKING)
# will create an import cycle from the view of a static type checker, which slows down type checking significantly.
from squid.bot._types import MessageableChannel
from squid.bot.actors import AccountIdCache, DiscordActorResolver
from squid.bot.catalogue.search.contextual import ContextualSearches
from squid.bot.diagnostics.gateway import GatewayHistory
from squid.bot.errors import UI_ERRORS, SquidCommandTree
from squid.bot.events.spec import extension as domain_events_extension
from squid.bot.extensions import Extension, ExtensionManager
from squid.bot.i18n import SquidAppCommandTranslator, localization_resolver
from squid.bot.interaction_metrics import InteractionMetrics
from squid.bot.members import DiscordMembers
from squid.bot.posts import BuildCardRenderer, PostReconciler, StarboardEntryRenderer, VoteSessionRenderer
from squid.bot.posts.submission_renderer import SubmissionStatusRenderer
from squid.bot.reactions import ReactionRouter, ReactionSubscription
from squid.bot.recount.spec import extension as recount_extension
from squid.bot.routes import AppRequest
from squid.bot.starboard.spec import extension as starboard_extension
from squid.bot.ui import HOST_DEFAULTS
from squid.bot.utils.uploads import CatboxClient
from squid.bot.utils.web import MediaPreviewClient
from squid.bot.voting.spec import extension as voting_extension
from squid.config import (
    BotIdentityConfig,
    BotProcessConfig,
    BuildConfig,
    CatboxConfig,
    NotificationConfig,
    load_bot_process_config,
    load_or_exit,
)
from squid.core.errors import RateLimitedError
from squid.core.observability import TraceSurface, correlation_scope, trace_span
from squid.core.posts.domain import ResourceKind
from squid.core.posts.topics import TopicPublisher, resource_topic
from squid.health import ProcessHealthServer
from squid.logging_config import configure_bot_logging
from squid.observability import configure_observability
from squid.runtime import (
    BackgroundTaskSupervisor,
    BotServices,
    RuntimeJob,
    start_log_capture,
    start_permission_epoch_watch,
)
from squid_reactivity import EventLedger, LocalTopicBus, observe_events
from squid_storage import PostgresTopicBridge
from squid_ui.content.text import localization_scope
from squid_ui.ext.discord import Config, DiscordUIRuntime, SessionManager, install
from squid_ui.profiling import MemoryProfiler
from squid_ui.routing import MountedRouter

logger = logging.getLogger(__name__)
type MaybeAwaitableFunc[**P, T] = Callable[P, T | Awaitable[T]]
DEFAULT_BOT_IDENTITY = BotIdentityConfig()
DEFAULT_CATBOX_CONFIG = CatboxConfig()
DEFAULT_BUILD_CONFIG = BuildConfig()
DEFAULT_NOTIFICATION_CONFIG = NotificationConfig()
CRITICAL_BOT_JOBS = frozenset({"discord-domain-events", "discord-reconciliation", "notification-deliveries"})
# The layout runtime is supervised but deliberately not health-critical: `is_healthy`
# reads job heartbeats, and forever-jobs such as the scheduler and challenge runner never
# write one merely by staying alive.

EXTENSIONS: tuple[Extension, ...] = (
    "squid.bot.messages_cog",
    "squid.bot.settings.cog",
    "squid.bot.guild_state_cog",
    "squid.bot.submission.cog",
    "squid.bot.log_cog",
    "squid.bot.help_cog",
    "squid.bot.feedback_cog",
    voting_extension,
    starboard_extension,
    recount_extension,
    "squid.bot.sync.cog",
    domain_events_extension,
    "squid.bot.notifications_cog",
    "squid.bot.accounts.cog",
    "squid.bot.admin_cog",
    "squid.bot.diagnostics.cog",
    "squid.bot.permissions_cog",
    "squid.bot.give_redstoner_cog",
    "squid.bot.version_tracking_cog",
    "squid.bot.welcome_relay_cog",
    "squid.bot.navigation.cog",
)
"""Extensions enabled in production."""

DEVELOPMENT_EXTENSIONS = ("jishaku", "squid.bot.devtools_cog", "squid.bot.showcase.cog")
"""Additional extensions loaded only in development mode."""


@dataclass(frozen=True, slots=True)
class MessageLookup:
    """The result of fetching a message from Discord."""

    message: discord.Message | None
    gone: bool = False
    """True when Discord says the message or its channel no longer exists."""


class RedstoneSquid(Bot):
    """Discord client owning transport services, UI sessions, and supervised jobs.

    The process runtime owns application services and outlives this client. Enter
    `background_tasks.running()` before starting the bot so extension jobs share
    one task group; `close` cancels that work before the runtime releases services.
    """

    def __init__(
        self,
        services: BotServices,
        config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
        *,
        catbox_config: CatboxConfig = DEFAULT_CATBOX_CONFIG,
        build_config: BuildConfig = DEFAULT_BUILD_CONFIG,
        notification_config: NotificationConfig = DEFAULT_NOTIFICATION_CONFIG,
        inference_model: str = "gpt-5.6-luna",
        inference_reasoning_effort: str = "low",
        development_mode: bool = False,
    ):
        self.services = services
        self.build_config = build_config
        self.notification_site_url = (
            None
            if notification_config.public_site_url is None
            else str(notification_config.public_site_url).rstrip("/")
        )
        self.inference_model = inference_model
        self.inference_reasoning_effort = inference_reasoning_effort
        self.development_mode = development_mode
        extensions = (*EXTENSIONS, *DEVELOPMENT_EXTENSIONS) if development_mode else EXTENSIONS
        self.extension_manager = ExtensionManager(extensions)
        description = f"{config.bot_name} v{config.bot_version}".strip()
        intents = discord.Intents(guilds=True, members=True, messages=True, message_content=True, reactions=True)
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.prefix),
            owner_id=config.owner_id,
            intents=intents,
            description=description or None,
            tree_cls=SquidCommandTree,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self.bot_name = config.bot_name
        self.command_limits = cast(SquidCommandTree[Self], self.tree).command_limits
        self.add_check(self._check_command_limit, call_once=True)
        self.bot_version = config.bot_version
        self.owner_server_id = config.owner_server_id
        self.source_code_url = config.source_code_url
        self.background_tasks = BackgroundTaskSupervisor()
        self.reactions = ReactionRouter(self, self.background_tasks)
        self._reaction_subscriptions: dict[str, ReactionSubscription] = {}
        self.post_reconciler = PostReconciler(
            self,
            [
                BuildCardRenderer(self),
                VoteSessionRenderer(self),
                StarboardEntryRenderer(self),
                SubmissionStatusRenderer(self),
            ],
        )
        self.catbox = CatboxClient(catbox_config)
        self.media_previews = MediaPreviewClient()
        # Permission checks resolve a Discord id to an account on every command,
        # and must never create one, so the lookup is cached rather than avoided.
        self.account_ids = AccountIdCache()
        self.actors = DiscordActorResolver(self)
        self.members = DiscordMembers(self)
        self.contextual_searches = ContextualSearches()
        self.interaction_metrics = InteractionMetrics()
        self.gateway_history = GatewayHistory()
        self.ui_router = MountedRouter[AppRequest]()
        self.layout_profiler = MemoryProfiler()
        self.action_ledger = EventLedger(limit=200)
        self.topic_bus = LocalTopicBus()
        self.topic_bridge: PostgresTopicBridge | None = None
        # Publishing goes through whichever of the two reaches every process. Until
        # `setup_hook` opens the bridge -- and forever, if no listener URL is configured --
        # that is the local bus, and the reconciler's poll is what the other processes get.
        self.topic_publisher: TopicPublisher = self.topic_bus
        # One assembly for the whole process, reachable from any interaction as
        # `DiscordUIRuntime.of(...)`: the session registry, the scheduler, and the challenge runner
        # a guard's dialog resumes an approved press through.
        self.ui: DiscordUIRuntime[RedstoneSquid] = install(
            self,
            Config(
                defaults=HOST_DEFAULTS,
                bus=self.topic_bus,
                profiler=self.layout_profiler,
                localization=localization_resolver,
                errors=UI_ERRORS,
            ),
            routes=self.ui_router,
        )

    @property
    def sessions(self) -> SessionManager:
        """The installed registry for live Discord sessions."""
        return self.ui.sessions

    @override
    async def add_cog(
        self,
        cog: commands.Cog,
        /,
        *,
        override: bool = False,
        guild: discord.abc.Snowflake | None = discord.utils.MISSING,
        guilds: Sequence[discord.abc.Snowflake] = discord.utils.MISSING,
    ) -> None:
        """Add a cog and subscribe its @reaction_handler methods, as discord.py does for listeners.

        The subscription starts after the cog has loaded, so handlers never run before cog_load.
        """
        await super().add_cog(cog, override=override, guild=guild, guilds=guilds)
        subscription = self.reactions.subscribe_if_present(cog)
        if subscription is not None:
            self._reaction_subscriptions[cog.qualified_name] = subscription

    @override
    async def remove_cog(
        self,
        name: str,
        /,
        *,
        guild: discord.abc.Snowflake | None = discord.utils.MISSING,
        guilds: Sequence[discord.abc.Snowflake] = discord.utils.MISSING,
    ) -> commands.Cog | None:
        """Detach the cog's reaction handlers, then remove it, so no new events arrive during unload."""
        subscription = self._reaction_subscriptions.pop(name, None)
        if subscription is not None:
            subscription.detach()
        return await super().remove_cog(name, guild=guild, guilds=guilds)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        self.interaction_metrics.observe(interaction)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reactions.dispatch_add(payload)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self.reactions.dispatch_remove(payload)

    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self.reactions.dispatch_clear(payload)

    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self.reactions.dispatch_clear_emoji(payload)

    async def _check_command_limit(self, ctx: commands.Context[Self]) -> bool:
        # Hybrid slash commands have already spent their unit in the command tree.
        if ctx.interaction is None:
            try:
                self.command_limits.check(ctx.author.id)
            except RateLimitedError as error:
                raise commands.CommandOnCooldown(
                    commands.Cooldown(12, 15), error.retry_after, commands.BucketType.user
                ) from error
        return True

    @override
    async def before_identify_hook(self, shard_id: int | None, *, initial: bool = False) -> None:
        self.gateway_history.record("identify", shard_id=shard_id)
        await super().before_identify_hook(shard_id, initial=initial)

    async def on_connect(self) -> None:
        self.gateway_history.record("connect")

    async def on_ready(self) -> None:
        self.gateway_history.record("ready")

    async def on_disconnect(self) -> None:
        self.gateway_history.record("disconnect")

    async def on_resumed(self) -> None:
        self.gateway_history.record("resume")

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
        command_name = ctx.command.qualified_name if ctx.command is not None else "unknown"
        attributes: dict[str, str | int] = {
            "squid.command.name": command_name,
            "squid.surface": TraceSurface.PREFIX_COMMAND,
        }
        if ctx.guild is not None:
            attributes["squid.guild.id"] = ctx.guild.id
        # A real Context always has a channel, but Context doubles built in tests (and any
        # partially constructed context) do not, and this attribute is not worth crashing over.
        if ctx.channel is not None:  # pyright: ignore[reportUnnecessaryComparison]
            attributes["squid.channel.id"] = ctx.channel.id
        with trace_span(f"discord.command {command_name}", attributes) as span, correlation_scope():
            request = await sd.request(ctx)
            with localization_scope(request.localization):
                await super().invoke(ctx)
                if ctx.command_failed:
                    span.set_error()

    @override
    async def start(self, token: str, *, reconnect: bool = True) -> None:
        """Own development diagnostics for the bot and the tasks it starts."""
        observation = observe_events(self.action_ledger) if self.development_mode else contextlib.nullcontext()
        with observation:
            await super().start(token, reconnect=reconnect)

    @override
    async def load_extension(self, name: str, *, package: str | None = None) -> None:
        self.extension_manager.check_live_operation(self._resolve_name(name, package))
        await super().load_extension(name, package=package)

    @override
    async def unload_extension(self, name: str, *, package: str | None = None) -> None:
        self.extension_manager.check_live_operation(self._resolve_name(name, package))
        await super().unload_extension(name, package=package)

    @override
    async def reload_extension(self, name: str, *, package: str | None = None) -> None:
        self.extension_manager.check_live_operation(self._resolve_name(name, package))
        await super().reload_extension(name, package=package)

    @override
    async def close(self) -> None:
        """Stop gateway-triggered and background work before application resources close."""
        await self.reactions.close()
        # Panels are left showing disabled controls rather than silently going dead. Bounded
        # because each one is a gateway round trip: a slow Discord must not stall shutdown,
        # and an undisabled panel times out on its own anyway.
        with anyio.move_on_after(3.0) as ui_shutdown:
            try:
                await self.ui.close()
            except Exception:
                logger.exception("UI cleanup failed during application shutdown")
        if ui_shutdown.cancel_called:
            logger.error("UI cleanup did not finish before the application shutdown deadline")
        await self.extension_manager.close(super().unload_extension)
        await self.background_tasks.close()
        # After the supervisor, so the bridge's listener is already cancelled and cannot
        # log a torn connection on the way out. Bounded, then hung up: shutdown must not
        # wait on a channel that only ever carried latency hints.
        if self.topic_bridge is not None:
            with anyio.move_on_after(3.0):
                await self.topic_bridge.pool.close()
            self.topic_bridge.pool.terminate()
        await self.catbox.aclose()
        await self.media_previews.aclose()
        await super().close()

    @override
    async def setup_hook(self) -> None:
        """Start shared services, load extensions, and register routes before gateway connection.

        If an extension fails to load, unload those already loaded in reverse order.
        The caller remains responsible for closing the bot and its background jobs.
        """
        self.background_tasks.capture_failures_into(self.services.error_reports)
        await self.tree.set_translator(SquidAppCommandTranslator())
        # Permission cache invalidation is needed regardless of which cogs are enabled.
        start_permission_epoch_watch(self.background_tasks, self.services.permission_epoch)
        self.background_tasks.start(self.ui.run(), name=RuntimeJob.LAYOUT)
        self.background_tasks.start(self.interaction_metrics.run(), name=RuntimeJob.INTERACTION_METRICS)
        if self.services.open_topic_bridge is not None:
            self.topic_bridge = await self.services.open_topic_bridge(self.topic_bus)
        if self.topic_bridge is not None:
            self.topic_publisher = self.topic_bridge
            self.background_tasks.start(self.topic_bridge.run(), name=RuntimeJob.TOPIC_BRIDGE)

        await self.extension_manager.start(super().load_extension, super().unload_extension)

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
        """Fetch a Discord message after resolving its channel from cache or the API.

        Return None when the channel is inaccessible, is not messageable, or the
        message cannot be found or read. This lookup does not change stored message
        facts. Other HTTP failures propagate to the caller.
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

    async def lookup_message(self, channel_id: int, message_id: int) -> MessageLookup:
        """Fetch a message and report whether Discord says it is gone.

        A 404 for the message or for its channel means the message is deleted. Missing
        access means nothing is known. Other HTTP failures propagate.
        """
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.NotFound:
                return MessageLookup(None, gone=True)
            except discord.Forbidden:
                return MessageLookup(None)
        if not isinstance(channel, MessageableChannel):
            return MessageLookup(None)
        try:
            return MessageLookup(await channel.fetch_message(message_id))
        except discord.NotFound:
            return MessageLookup(None, gone=True)
        except discord.Forbidden:
            return MessageLookup(None)

    async def fetch_stored_message(self, channel_id: int, message_id: int) -> discord.Message | None:
        """Fetch a message the bot did not post and bring its stored copy up to date.

        Edits and deletions of such messages normally arrive as events, which are lost
        while the bot is offline. Whoever uses the message checks it through this method
        instead of sweeping every stored message. The stored content is refreshed when the
        message is found and the row is marked deleted when Discord says it is gone;
        messages the database does not store are left alone.

        Returns:
            The message, or None if it is gone or cannot be read.
        """
        lookup = await self.lookup_message(channel_id, message_id)
        if lookup.message is not None:
            message = lookup.message
            edited_at = None if message.edited_at is None else Instant(message.edited_at)
            await self.services.messages.refresh_content(message.id, message.content, edited_at)
        elif lookup.gone:
            await self.services.messages.mark_deleted(message_id)
        return lookup.message

    async def refresh_posts(self, resource_kind: ResourceKind, resource_key: str) -> None:
        """Publish a topic hint and reconcile any pending generation for a resource.

        Callers must first enqueue durable post work through the application service.
        This method does not create a pending generation; background reconciliation
        can retry one if immediate delivery fails.
        """
        self.topic_publisher.publish(resource_topic(resource_kind, resource_key))
        generation = await self.services.posts.pending_generation(resource_kind, resource_key)
        if generation is None:
            return
        await self.post_reconciler.reconcile(resource_kind, resource_key, generation)


async def _run_bot(
    config: BotProcessConfig,
    identity_config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
) -> None:
    """Run the configured Discord process until the gateway client stops."""
    async with create_bot_runtime(config.runtime) as runtime:
        bot = RedstoneSquid(
            runtime.services,
            config=identity_config,
            catbox_config=config.catbox,
            build_config=config.build,
            notification_config=config.notification,
            inference_model=config.openai.chat_model,
            inference_reasoning_effort=config.openai.reasoning_effort,
            development_mode=config.development_mode,
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
            ProcessHealthServer(bot_ready, port=config.bot.health_port),
        ):
            start_log_capture(
                bot.background_tasks,
                runtime.services.error_reports,
                enabled=config.diagnostics.capture_logged_errors,
                capacity=config.diagnostics.log_capture_queue,
            )
            await bot.start(config.discord.token.get_secret_value())


async def main(
    process_config: BotProcessConfig | None = None,
    identity_config: BotIdentityConfig = DEFAULT_BOT_IDENTITY,
) -> None:
    """Run the Discord process with process-owned logging and telemetry."""
    resolved_config = process_config or load_or_exit(load_bot_process_config)
    queue_listener = configure_bot_logging(resolved_config.logging, dev_mode=resolved_config.development_mode)
    observability = configure_observability(resolved_config.observability, service_name="bot")

    try:
        await _run_bot(resolved_config, identity_config)
    finally:
        observability.shutdown()
        queue_listener.stop()


if __name__ == "__main__":
    # Support running this module directly as well as through the process launcher.
    asyncio.run(main())
