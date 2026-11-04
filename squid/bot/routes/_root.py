"""Route persistent Discord controls to feature handlers.

A routed control carries an action ID in Discord's custom_id, so it can still be
handled after the original UI session or bot process has ended. Feature modules
register handlers beneath the r: namespace; those handlers recover the resource
from the route arguments or the clicked message. This module supplies the shared
router, tracing, and unavailable-control/error replies.
"""

from typing import TYPE_CHECKING, override

import discord

import squid_ui_discord as sd
from squid.bot.errors import handle_interaction_error
from squid.bot.ui import text_node
from squid.core.i18n import tr
from squid.observability import SpanAttribute, TraceSurface, correlation_scope, trace_span

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


routes: sd.routing.RouteGroup[RedstoneSquid] = sd.routing.RouteGroup("r")
"""The ordinary root group reserving the bot's durable ``r:`` namespace."""

_FEATURE_GROUPS: dict[str, sd.routing.RouteGroup[RedstoneSquid]] = {}


def _feature_group(prefix: str) -> tuple[sd.routing.RouteGroup[RedstoneSquid], bool]:
    """Return the cached feature group and whether it was newly created.

    Callers use the boolean to install middleware only once across extension reloads.
    """
    if group := _FEATURE_GROUPS.get(prefix):
        return group, False
    group = routes.group(prefix)
    _FEATURE_GROUPS[prefix] = group
    return group, True


class TraceRoutes[BotT: discord.Client](sd.routing.Middleware[BotT]):
    """Trace every routed interaction without recording user-controlled route values."""

    @override
    async def dispatch(self, request: sd.routing.RouteRequest[BotT], proceed: sd.routing.RouteProceed) -> None:
        attributes: dict[str, SpanAttribute] = {
            "squid.surface": TraceSurface.DISCORD_ROUTE,
            "squid.route.component": request.component.value,
            "squid.route.matched_alias": request.matched_alias,
        }
        if request.route is not None:
            attributes["squid.route.format"] = request.route.format
        if request.group_prefix is not None:
            attributes["squid.route.group"] = request.group_prefix
        with trace_span("discord.route", attributes), correlation_scope():
            await proceed()


async def _route_gone_hook(interaction: discord.Interaction[RedstoneSquid]) -> None:
    request = await sd.request(interaction)
    await request.respond(
        text_node(tr(t"This control is no longer available.")),
        audience="personal",
    )


async def _route_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    await handle_interaction_error(interaction, error, surface=source)


router: sd.routing.Router[RedstoneSquid] = sd.routing.Router(
    namespace=routes,
    on_gone=_route_gone_hook,
    on_error=_route_error_hook,
)
router.add_middleware(TraceRoutes())
