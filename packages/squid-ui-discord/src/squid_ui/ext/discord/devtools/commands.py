"""Discord commands for inspecting live UI sessions and operating their runtimes.

DevTools uses the bot owner's identity by default; hosts can supply another authorization
check. The dashboard uses DevToolsRuntime for snapshots and operations, whose policy
separately controls which operations are enabled.
"""

from collections.abc import Awaitable, Callable
from typing import Literal, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from squid_i18n import source_text
from squid_reactivity.diagnostics import (
    EventLedger,
)
from squid_reactivity.topics import TopicBus
from squid_ui.chrome import DEFAULT_CHROME
from squid_ui.ext.discord.access import Owner
from squid_ui.ext.discord.cog import Cog
from squid_ui.ext.discord.contracts import ResponseSource
from squid_ui.ext.discord.devtools.runtime import (
    DevToolsRuntime,
)
from squid_ui.ext.discord.devtools.views import (
    DevToolsSection,
    OperationalInspector,
)
from squid_ui.ext.discord.messages.root import MessageRoot
from squid_ui.ext.discord.messages.scheduler import MessageRootScheduler
from squid_ui.ext.discord.request import request
from squid_ui.ext.discord.response import Presented
from squid_ui.ext.discord.sessions import SessionManager
from squid_ui.profiling import Profiler

type DevToolsCheck[BotT: commands.Bot] = Callable[[Context[BotT]], Awaitable[bool]]

SESSION_SECONDS = 300


def _command_wording(field: str) -> app_commands.locale_str:
    """Expose a Chrome field to the host's discord.py command translator."""
    return app_commands.locale_str(source_text(getattr(DEFAULT_CHROME, field), format="plain"), chrome_field=field)


async def _owner_only[BotT: commands.Bot](ctx: Context[BotT]) -> bool:
    """Use discord.py's configured owner as the safe default authorization policy."""
    return await ctx.bot.is_owner(ctx.author)


async def open_devtools(
    source: ResponseSource,
    *,
    runtime: DevToolsRuntime,
    action_ledger: EventLedger | None = None,
    focus: DevToolsSection | str = DevToolsSection.OVERVIEW,
    message_root_id: str | None = None,
    session_id: str | None = None,
    timeout: float | None = SESSION_SECONDS,
) -> MessageRoot:
    """Open a caller-owned dashboard until it closes, times out, or its host stops.

    The source must belong to an installed UI runtime. Its request scope owns the
    dashboard. The host command authorizes the caller before opening; the devtools
    runtime policy controls which operations the dashboard may execute.
    """
    current = await request(source)
    client = current.client
    inspector = OperationalInspector(
        runtime,
        client=client if isinstance(client, discord.Client) else None,
        action_ledger=action_ledger,
    )
    inspector.section = focus.value if isinstance(focus, DevToolsSection) else focus
    inspector.message_root_id = message_root_id
    inspector.session_id = session_id
    result = await current.respond(
        inspector,
        timeout=timeout,
        audience="personal" if current.interaction is not None else "public",
        access=Owner(current.user.id),
        session=None,
    )
    if not isinstance(result, Presented):
        message = "devtools dashboard delivery was abandoned"
        raise RuntimeError(message)  # noqa: TRY004 - Delivery failed; the caller supplied valid content.
    return result.root


class DevTools[BotT: commands.Bot](Cog[BotT]):
    """The unified ``!dev ui`` operational control plane.

    Pass a bot with an installed UI runtime; unloading the cog closes its dashboards.
    Supply an EventLedger observed by the host's observe_events scope to show
    action and background-work diagnostics. Creating this cog starts no observers.
    """

    def __init__(
        self,
        bot: BotT,
        check: DevToolsCheck[BotT] = _owner_only,
        manager: SessionManager | None = None,
        *,
        profiler: Profiler | None = None,
        scheduler: MessageRootScheduler | None = None,
        bus: TopicBus | None = None,
        runtime: DevToolsRuntime | None = None,
        action_ledger: EventLedger | None = None,
    ) -> None:
        super().__init__(bot)
        self._check = check
        self._runtime = runtime or DevToolsRuntime(
            sessions=manager,
            scheduler=scheduler,
            bus=bus,
            profiler=profiler,
        )
        self._manager = self._runtime.sessions
        self._scheduler = self._runtime.scheduler
        self._bus = self._runtime.bus
        self._profiler = self._runtime.profiler
        self._action_ledger = action_ledger or EventLedger(limit=200)

    @override
    # pyrefly: ignore[bad-override]  # MaybeCoro[bool] covers a coroutine; pyrefly drops the parameter
    async def cog_check(self, ctx: Context[BotT]) -> bool:  # pyright: ignore[reportIncompatibleMethodOverride]
        """Authorize every command through the single injected gate."""
        return await self._check(ctx)

    @commands.hybrid_group(
        _command_wording("dev_name"),
        hidden=True,
        description=_command_wording("dev_help"),
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def dev_group(self, ctx: Context[BotT]) -> None:
        """Development-only operational diagnostics."""
        await ctx.send_help("dev")

    @dev_group.command(
        _command_wording("dev_ui_name"),
        description=_command_wording("dev_ui_help"),
    )
    @app_commands.rename(tab=_command_wording("dev_ui_tab_name"))
    @app_commands.describe(tab=_command_wording("dev_ui_tab_help"))
    @app_commands.choices(
        tab=[
            app_commands.Choice(name=_command_wording("dev_ui_choice_overview"), value="overview"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_roots"), value="roots"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_sessions"), value="sessions"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_routes"), value="routes"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_activity"), value="activity"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_queues"), value="queues"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_profile"), value="profile"),
            app_commands.Choice(name=_command_wording("dev_ui_choice_persistence"), value="persistence"),
        ]
    )
    async def ui_command(
        self,
        ctx: Context[BotT],
        tab: Literal[
            "overview", "roots", "sessions", "routes", "activity", "queues", "profile", "persistence"
        ] = "overview",
    ) -> None:
        """Open the operational dashboard on the selected tab."""
        await open_devtools(ctx, runtime=self._runtime, action_ledger=self._action_ledger, focus=tab)


__all__ = ["DevTools", "DevToolsCheck", "open_devtools"]
