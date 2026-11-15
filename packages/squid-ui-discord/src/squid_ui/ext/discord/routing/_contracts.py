"""Route identities, callable handles, and middleware contracts shared by registration and dispatch."""

import annotationlib
import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import update_wrapper
from typing import Any

import discord
from squid_ui.interactions.routing import Route

type RouteHandler = Callable[..., Awaitable[None]]
"""Storage type. The checked surface is `Router.route`, which pins the first argument."""

type RouteLike = Route | str
"""A route, or the format string to build one from."""

type GoneHook[BotT: discord.Client] = Callable[[discord.Interaction[BotT]], Awaitable[None]]
"""A friendly response for a control retired from a reserved router namespace."""

type RouteProceed = Callable[[], Awaitable[None]]
"""Proceed through the remaining middleware to the already-resolved handler."""


class RouteComponent(StrEnum):
    """The Discord component type playing the role of an HTTP method."""

    BUTTON = "button"
    SELECT = "select"


@dataclass(frozen=True, slots=True)
class RouteDescription:
    """A stable, read-only description of one registered routed control."""

    component: RouteComponent
    format: str
    params: tuple[tuple[str, str], ...]
    aliases: tuple[str, ...]
    handler_module: str
    handler_qualname: str
    group_prefix: str | None = None
    middleware: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteRequest[BotT: discord.Client]:
    """Immutable dispatch facts supplied to routed-control middleware."""

    interaction: discord.Interaction[BotT]
    component: RouteComponent
    route_id: str
    route: Route | None
    params: Mapping[str, Any]
    values: tuple[str, ...]
    group_prefix: str | None
    matched_alias: bool


class Middleware[BotT: discord.Client](ABC):
    """A reusable routed-control policy attached to a router or route group."""

    @abstractmethod
    async def dispatch(self, request: RouteRequest[BotT], proceed: RouteProceed) -> None:
        """Continue once through ``proceed``, or return to short-circuit."""


@dataclass(frozen=True, slots=True)
class _Registration:
    route: Route
    handler: RouteHandler
    component: RouteComponent
    accepts: frozenset[str] | None
    """Parameter names to pass, or None for a handler taking `**kwargs`."""
    group_prefix: str | None = None


class RouteHandle[**P]:
    """A callable action whose identity survives handler reloads.

    The owning router or group retains this handle. Redeclaring the same route and
    component replaces its validated binding, so previously imported handles call the
    latest handler. Calls already in progress finish with their original handler.
    Direct calls bypass router middleware and acknowledgement handling.

    The call signature follows the decorated function; ID parameters are validated at
    runtime by Route.id. Reloaded handlers must remain compatible with direct callers.
    """

    __wrapped__: RouteHandler
    __qualname__: str

    def __init__(self, registration: _Registration) -> None:
        self._bind(registration)

    def _bind(self, registration: _Registration) -> None:
        update_wrapper(self, registration.handler, updated=())
        self._registration = registration

    @property
    def route(self) -> Route:
        """The immutable route used to generate and match custom IDs."""
        return self._registration.route

    def id(self, **params: object) -> str:
        """Build a canonical custom ID, validating parameters against the route."""
        return self.route.id(**params)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> None:
        await self._registration.handler(*args, **kwargs)


def _accepted(route: Route, handler: RouteHandler, component: RouteComponent) -> frozenset[str] | None:
    """Which of ``route``'s parameters ``handler`` asked for, rejecting names it invented."""
    # FORWARDREF, because evaluating a handler's annotations here would resurrect the
    # TYPE_CHECKING-only client import that PEP 649 exists to keep deferred. Only names and
    # kinds are wanted anyway.
    signature = inspect.signature(handler, annotation_format=annotationlib.Format.FORWARDREF)
    parameters = list(signature.parameters.values())
    required = 2 if component is RouteComponent.SELECT else 1
    detail = "the interaction and selected values" if component is RouteComponent.SELECT else "the interaction"
    if len(parameters) < required:
        message = f"route handler {handler.__qualname__!r} must take {detail} first"
        raise ValueError(message)
    positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    for parameter in parameters[:required]:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            break
        if parameter.kind not in positional:
            message = (
                f"route handler {handler.__qualname__!r} must take {detail} positionally, "
                f"but {parameter.name!r} is {parameter.kind.description}"
            )
            raise ValueError(message)
    unfeedable = [
        parameter.name for parameter in parameters[required:] if parameter.kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    if unfeedable:
        message = (
            f"route handler {handler.__qualname__!r} takes {unfeedable} positional-only, "
            "but route parameters are passed by name"
        )
        raise ValueError(message)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return None
    named = {
        parameter.name
        for parameter in parameters[required:]
        if parameter.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unknown = named - set(route.params)
    if unknown:
        message = (
            f"route handler {handler.__qualname__!r} asks for {sorted(unknown)}, "
            f"which route {route.format!r} does not carry: it has {list(route.params)}"
        )
        raise ValueError(message)
    return frozenset(named)
