"""Endpoint declarations and delivery policy for Discord extensions."""

from typing import TYPE_CHECKING, Any, cast

import anyio
import discord

import squid_ui.ext.discord as sd
from squid.bot.ui import text_node
from squid.core.actors import ActingContext, UserActor, current_acting_context
from squid.core.observability import TraceSurface, correlation_scope, trace_span
from squid_i18n import TextLike
from squid_ui.content.text import localization_scope
from squid_ui.routing import Endpoint, Proceed, RouteMatch, Router

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


type AppRequest = sd.Request[Any, RedstoneSquid]


class ScreenUnavailable(Exception):
    """Send a localized explanation when a screen cannot open."""

    def __init__(self, message: TextLike) -> None:
        super().__init__()
        self.message = message


def response_policy(endpoint: Endpoint[AppRequest, ...]) -> sd.ResponseSpec:
    if isinstance(endpoint.policy, sd.ResponseSpec):
        return sd.ResponseSpec(audience="personal").overlay(endpoint.policy)
    if endpoint.owner is not None and issubclass(endpoint.owner, sd.Screen):
        return endpoint.owner.__response_spec__
    return sd.ResponseSpec(audience="personal")


async def deliver(request: AppRequest, match: RouteMatch[AppRequest], proceed: Proceed) -> Any:
    """Apply the same tracing, acknowledgement, and delivery to every invocation."""
    policy = response_policy(match.endpoint)
    deferral = "public" if policy.audience == "public" else "private"

    async def acknowledge() -> None:
        await anyio.sleep(1.0)
        if request.interaction is not None and request.interaction.response.is_done():
            return
        try:
            await request.defer(deferral)
        except discord.InteractionResponded:
            pass
        except discord.HTTPException as error:
            if error.code != 40060:
                raise

    attributes = {
        "squid.surface": TraceSurface.DISCORD_ROUTE,
        "squid.route.format": match.endpoint.path,
    }
    context = current_acting_context.get()
    if (
        context is None
        or not isinstance(context.operator, UserActor)
        or context.operator.discord_user_id != request.user.id
    ):
        actor = UserActor(discord_user_id=request.user.id, guild_id=request.guild.id if request.guild else None)
        context = ActingContext.direct(actor)
    with (
        context.scope(),
        localization_scope(request.localization),
        trace_span("ui.route", attributes),
        correlation_scope(),
    ):
        request.client.command_limits.check(request.user.id)
        failure: Exception | None = None
        result: Any = None
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(acknowledge)
            try:
                try:
                    result = await proceed()
                except ScreenUnavailable as error:
                    result = sd.Response(text_node(error.message))
                if isinstance(result, sd.Screen):
                    result = await request.respond(result)
                elif isinstance(result, sd.Response):
                    audience = cast(sd.Audience, request.audience or policy.audience)
                    result = await request.respond(result, audience=audience)
            except Exception as error:
                # Preserve domain exceptions rather than wrapping them in a task-group error.
                failure = error
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
        return result


class AppRoutes(Router[AppRequest]):
    def __init__(self) -> None:
        super().__init__()
        self.add_middleware(deliver)
