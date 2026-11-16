"""The application's single registry for screen openings and persistent actions."""

from collections.abc import Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import anyio
import discord

import squid_ui.ext.discord as sd
from squid.bot.ui import text_node
from squid.observability import TraceSurface, correlation_scope, trace_span
from squid_i18n import TextLike
from squid_ui.content.text import localization_scope
from squid_ui.routing import Endpoint, Proceed, RouteMatch, Router

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


type AppRequest = sd.Request[Any, RedstoneSquid]


@dataclass(frozen=True, slots=True)
class ScreenOpening:
    screen: sd.Screen[Any, Any, Any]
    session_key: Hashable | None = None


class ScreenUnavailable(Exception):
    """Send a localized explanation under the endpoint's response policy."""

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
        await anyio.sleep(2.5)
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
    with localization_scope(request.localization), trace_span("ui.route", attributes), correlation_scope():
        failure: Exception | None = None
        result: Any = None
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(acknowledge)
            try:
                try:
                    result = await proceed()
                except ScreenUnavailable as error:
                    result = await request.respond(
                        text_node(error.message), audience=cast(sd.Audience, policy.audience)
                    )
                else:
                    if isinstance(result, ScreenOpening):
                        result = await request.respond(result.screen, session_key=result.session_key)
                    elif isinstance(result, sd.Response):
                        result = await request.respond(result, audience=cast(sd.Audience, policy.audience))
            except Exception as error:
                # Preserve domain exceptions rather than wrapping them in a task-group error.
                failure = error
            finally:
                tasks.cancel_scope.cancel()
        if failure is not None:
            raise failure
        return result


router = Router[AppRequest]()
router.add_middleware(deliver)
