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

from squid_i18n import tr
from squid_reactivity.diagnostics import (
    EventLedger,
)
from squid_reactivity.topics import TopicBus
from squid_ui.profiling import Profiler
from squid_ui_discord.access import Owner
from squid_ui_discord.cog import Cog
from squid_ui_discord.contracts import ResponseSource
from squid_ui_discord.devtools_runtime import (
    DevToolsRuntime,
)
from squid_ui_discord.devtools_view import (
    DevToolsSection,
    OperationalInspector,
)
from squid_ui_discord.i18n import to_locale_str
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_scheduler import MessageRootScheduler
from squid_ui_discord.request import request
from squid_ui_discord.response import Presented
from squid_ui_discord.sessions import SessionManager

type DevToolsCheck[BotT: commands.Bot] = Callable[[Context[BotT]], Awaitable[bool]]

SESSION_SECONDS = 300


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
        to_locale_str(tr(t"command-dev-name = dev")),
        hidden=True,
        description=to_locale_str(tr(t"command-dev-help = Development-only operational diagnostics.")),
    )
    @discord.app_commands.default_permissions(administrator=True)
    async def dev_group(self, ctx: Context[BotT]) -> None:
        """Development-only operational diagnostics."""
        await ctx.send_help("dev")

    @dev_group.command(
        to_locale_str(tr(t"command-dev-ui-name = ui")),
        description=to_locale_str(tr(t"command-dev-ui-help = Open the unified operational dashboard.")),
    )
    @app_commands.rename(tab=to_locale_str(tr(t"command-dev-ui-tab-name = tab")))
    @app_commands.describe(tab=to_locale_str(tr(t"command-dev-ui-tab-help = The dashboard tab to open first.")))
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
