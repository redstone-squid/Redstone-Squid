"""Root route group, router lifecycle hooks, and process-wide middleware."""

from typing import TYPE_CHECKING

import discord

import squid_ui_discord as sd
from squid.observability import SpanAttribute, correlation_scope, trace_span

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


routes: sd.routing.RouteGroup[RedstoneSquid] = sd.routing.RouteGroup("r")
"""The root group reserving the bot's durable `r:` custom-id namespace; every feature group hangs off it."""

_FEATURE_GROUPS: dict[str, sd.routing.RouteGroup[RedstoneSquid]] = {}
_FEATURE_ROUTES: dict[tuple[str, str], sd.routing.Route] = {}


def _feature_group(prefix: str) -> tuple[sd.routing.RouteGroup[RedstoneSquid], bool]:
    """Return a feature-owned group and whether this import created it."""
    if group := _FEATURE_GROUPS.get(prefix):
        return group, False
    group = routes.group(prefix)
    _FEATURE_GROUPS[prefix] = group
    return group, True


def _feature_route(
    group: sd.routing.RouteGroup[RedstoneSquid], format: str, *, aliases: tuple[str, ...] = ()
) -> sd.routing.Route:
    """Define a route once, so discord.py may reload the handler module without redefining its identity.

    Raises:
        ValueError: A reload changed the aliases of an already-defined route, which would strand
            custom ids on live messages.
    """
    key = (group.prefix, format)
    if route := _FEATURE_ROUTES.get(key):
        if route.aliases != aliases:
            message = f"reloaded route {route.format!r} changed aliases"
            raise ValueError(message)
        return route
    route = group.define(format, aliases=aliases)
    _FEATURE_ROUTES[key] = route
    return route


class TraceRoutes[BotT: discord.Client](sd.routing.Middleware[BotT]):
    """Open a span and a correlation scope around every routed interaction.

    Records the route format and group, never the matched values: those come from a custom id a
    user could have crafted.
    """

    async def dispatch(self, request: sd.routing.RouteRequest[BotT], proceed: sd.routing.RouteProceed) -> None:
        attributes: dict[str, SpanAttribute] = {
            "squid.surface": "discord_route",
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
    from squid.bot.ui import text_node
    from squid.core.i18n import tr

    await interaction.client.app_ui.respond(
        interaction,
        text_node(tr("This control is no longer available.")),
        audience="personal",
    )


async def _route_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Keep the route hook lazy so importing the router does not import the command UI catalogue.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=source)


router: sd.routing.Router[RedstoneSquid] = sd.routing.Router(
    namespace=routes,
    on_gone=_route_gone_hook,
    on_error=_route_error_hook,
)
router.add_middleware(TraceRoutes())
