"""Dispatch controls whose identity is stored in their Discord custom ID.

A Router matches an incoming custom ID against registered Routes and passes the extracted
parameters to a handler. Re-registering the same routes at startup lets controls on old
messages work after a process restart; no live MessageRoot or in-memory handler binding
is needed for those messages.

    @router.route("build:edit:{build_id:int}")
    async def edit_build(interaction: discord.Interaction[Bot], build_id: int) -> None: ...

Decorated handlers expose ``.id(...)`` to build control IDs and ``.route`` for the
underlying Route. Their handles stay current when the same router or group is reloaded.
Parameter names are checked when the handler registers, so a misspelling fails
at registration instead of on a click. Call ``router.register(client)`` after defining
routes to install dispatch; existing handlers can later be replaced during reloads.

Handlers receive the raw Discord interaction so they can read the message and look up
application state. They supply their own authorization, concurrency control, and
transactions. MessageRoot's action lock and render-generation checks do not apply.

Use ``squid_ui.ext.discord.testing.interaction_harness`` with ``Router.dispatch`` to exercise
handlers without a bot, gateway, or real message.
"""

from squid_ui.routing import Route

from ._client import routers
from ._contracts import (
    GoneHook,
    Middleware,
    RouteComponent,
    RouteDescription,
    RouteHandle,
    RouteHandler,
    RouteLike,
    RouteProceed,
    RouteRequest,
)
from ._groups import RouteGroup
from ._router import Router

__all__ = [
    "GoneHook",
    "Middleware",
    "Route",
    "RouteComponent",
    "RouteDescription",
    "RouteGroup",
    "RouteHandle",
    "RouteHandler",
    "RouteLike",
    "RouteProceed",
    "RouteRequest",
    "Router",
    "routers",
]
