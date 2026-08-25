"""Root route group, router lifecycle hooks, and process-wide middleware."""

from typing import TYPE_CHECKING

import discord

import squid_discord as sd
from squid.observability import SpanAttribute, correlation_scope, trace_span

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


routes: sd.routing.RouteGroup[RedstoneSquid] = sd.routing.RouteGroup("r")
"""The ordinary root group reserving the bot's durable ``r:`` namespace."""


class TraceRoutes[BotT: discord.Client](sd.routing.Middleware[BotT]):
    """Trace every routed interaction without recording user-controlled route values."""

    async def dispatch(
        self, request: sd.routing.RouteRequest[BotT], proceed: sd.routing.RouteProceed
    ) -> None:
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
    from squid.bot.i18n import resolve_locale, t
    from squid.bot.ui import respond_presentation, text_layout
    from squid.core.i18n import _

    locale = await resolve_locale(interaction, interaction.client.services.settings)
    await respond_presentation(interaction, text_layout(t(locale, _("This control is no longer available."))))


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
