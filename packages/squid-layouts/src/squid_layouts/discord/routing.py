"""Dispatch for stateless routed controls.

The counterpart to `Mount` for the tier that outlives the process. A mount owns handlers
in memory and re-issues ids per generation; a router owns a table of `Route`s and resolves
an incoming custom id against it, so a button drawn a year ago by a since-restarted
process still reaches code.

A handler takes the interaction and then its route's parameters by name, the way a Flask
view takes its path variables:

    @router.route("build:edit:{build_id:int}")
    async def edit_build(interaction: discord.Interaction[Bot], build_id: int) -> None: ...

A named `Route` is worth spelling out when something outside the handler's module has to
build ids from it — which is the usual case, since the card that draws the button rarely
lives beside the code that answers it.

Names are checked against the route when the handler registers, so a typo is an import
error rather than a click that fails in production. The interaction stays raw and is a
deliberate asymmetry with `Action`: a routed control's state lives in the message and the
store, so handlers need `interaction.message`, `interaction.guild` and the client — facts
no portable event can carry. The *node* stays portable; only dispatch is Discord's, which
is why this lives here rather than beside the semantic layer.

None of the mount's guarantees apply: no author lock, no generation check, no transaction.
A routed handler owns its own concurrency.

`squid_layouts.discord.testing.fake_interaction` plus `Router.dispatch` is this module's
test client — a handler can be exercised without a bot, a gateway or a real message.
"""

import annotationlib
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Concatenate, Self, override

import discord

from squid_layouts.discord.mount import ErrorHook
from squid_layouts.routing import Route

logger = logging.getLogger(__name__)

type RouteHandler = Callable[..., Awaitable[None]]
"""Storage type. The checked surface is `Router.route`, which pins the first argument."""

type RouteLike = Route | str
"""A route, or the format string to build one from — `Route` is only worth naming when
something outside the handler's module has to build ids from it."""

type GoneHook[BotT: discord.Client] = Callable[[discord.Interaction[BotT]], Awaitable[None]]
"""A friendly response for a control retired from a reserved router namespace."""


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


@dataclass(frozen=True, slots=True)
class _Registration:
    route: Route
    handler: RouteHandler
    component: RouteComponent
    accepts: frozenset[str] | None
    """Parameter names to pass, or None for a handler taking `**kwargs`."""


def _accepted(route: Route, handler: RouteHandler, component: RouteComponent) -> frozenset[str] | None:
    """Which of ``route``'s parameters ``handler`` asked for, rejecting names it invented."""
    # FORWARDREF, because evaluating a handler's annotations here would resurrect the
    # TYPE_CHECKING-only client import that PEP 649 exists to keep deferred. Only names and
    # kinds are wanted anyway.
    signature = inspect.signature(handler, annotation_format=annotationlib.Format.FORWARDREF)
    parameters = list(signature.parameters.values())
    required = 2 if component is RouteComponent.SELECT else 1
    if len(parameters) < required:
        detail = "the interaction and selected values" if component is RouteComponent.SELECT else "the interaction"
        message = f"route handler {handler.__qualname__!r} must take {detail} first"
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


class Router[BotT: discord.Client]:
    """A table of routes and their handlers, registered once with the client."""

    def __init__(
        self,
        *,
        namespace: str | None = None,
        on_gone: GoneHook[BotT] | None = None,
        on_error: ErrorHook | None = None,
    ) -> None:
        if namespace is not None and (not namespace or ":" in namespace):
            message = "a router namespace must be one non-empty route segment"
            raise ValueError(message)
        if namespace is not None and on_gone is None:
            message = "a namespaced router needs an on_gone hook for retired controls"
            raise ValueError(message)
        self._routes: list[_Registration] = []
        self._registered = False
        self.namespace = namespace
        self.on_gone = on_gone
        self.on_error = on_error

    def describe(self) -> tuple[RouteDescription, ...]:
        """Describe the current route table without exposing mutable registrations."""
        return tuple(
            RouteDescription(
                component=registration.component,
                format=registration.route.format,
                params=tuple(
                    (name, converter.name)
                    for name, converter in zip(registration.route.params, registration.route.converters, strict=True)
                ),
                aliases=registration.route.aliases,
                handler_module=registration.handler.__module__,
                handler_qualname=registration.handler.__qualname__,
            )
            for registration in self._routes
        )

    def route[**P](
        self, route: RouteLike
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]]],
        Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]],
    ]:
        """Register ``route``'s handler, as a decorator.

        `Concatenate` pins the first argument to this router's client, so a handler reaches
        `interaction.client` typed instead of re-annotating it. The rest of the signature is
        captured rather than constrained — a checker cannot read parameter names out of a
        format string — which is why `add` re-checks them at import.
        """

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]],
        ) -> Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]]:
            self.add(route, handler)
            return handler

        return decorate

    def select[**P](
        self, route: RouteLike
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]]],
        Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]],
    ]:
        """Register a routed string-select handler receiving values before path parameters."""

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]],
        ) -> Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]]:
            self.add(route, handler, component=RouteComponent.SELECT)
            return handler

        return decorate

    def add(
        self,
        route: RouteLike,
        handler: RouteHandler,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
    ) -> None:
        """Register ``route``'s handler; re-registering the same route replaces it.

        Replacement rather than rejection because loading an extension re-executes its
        module, so a reload registers every route in it a second time with a fresh function
        object. The newest handler wins, exactly as it did when these were `DynamicItem`
        classes keyed by template. It also leaves the template unchanged, which is why a
        reload is allowed after `register` while a genuinely new route is not.

        A route that *shadows* another is rejected outright, by `Route.overlaps`, which is
        exact rather than sampled: the segment grammar makes "could one id belong to both"
        a per-position decision. So resolution order cannot decide which handler a click
        reaches, because an ambiguous table never registers in the first place.
        """
        route = Route(route) if isinstance(route, str) else route
        if route.accepts_first_segment("ctl"):
            message = f"route {route.format!r} enters the reserved mount namespace 'ctl:'"
            raise ValueError(message)
        if self.namespace is not None and (not route.canonical_starts_with(self.namespace) or len(route.segments) == 1):
            message = f"route {route.format!r} must live under the reserved namespace {self.namespace!r}"
            raise ValueError(message)
        registration = _Registration(route, handler, component, _accepted(route, handler, component))
        replaced: int | None = None
        for index, existing in enumerate(self._routes):
            if existing.component is component and existing.route.format == route.format:
                replaced = index
                break
        for index, existing in enumerate(self._routes):
            if index == replaced:
                continue
            if existing.component is component and route.overlaps(existing.route):
                message = f"route {route.format!r} overlaps the already-registered {existing.route.format!r}"
                raise ValueError(message)
        if replaced is not None:
            existing = self._routes[replaced]
            if self._registered and existing.route.aliases != route.aliases:
                message = (
                    f"route {route.format!r} cannot change aliases after Router.register(client) builds the template"
                )
                raise RuntimeError(message)
            self._routes[replaced] = registration
            return
        if self._registered:
            message = f"new route {route.format!r} must be added before Router.register(client) builds the template"
            raise RuntimeError(message)
        self._routes.append(registration)

    def resolve(
        self,
        custom_id: str,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
    ) -> tuple[_Registration, Mapping[str, Any]] | None:
        """The registration owning ``custom_id`` and the parameters it carries, if any."""
        for registration in self._routes:
            if registration.component is not component:
                continue
            params = registration.route.match(custom_id)
            if params is not None:
                return registration, params
        return None

    def template(self) -> re.Pattern[str]:
        """The one pattern covering every registered route.

        Groups are stripped because discord.py's `ViewStore.dispatch_dynamic_items`
        schedules *every* template that matches a custom id: one class over an alternation
        makes a double dispatch structurally impossible, where one class per route would
        make it a naming accident away. Parameters are read back by re-matching the
        individual route in `resolve`, which also keeps two routes from colliding over a
        shared group name.
        """
        if not self._routes:
            # An alternation of nothing matches everything; match nothing instead.
            patterns: list[str] = []
        else:
            patterns = [f"(?:{registration.route.anonymous})" for registration in self._routes]
        if self.namespace is not None:
            patterns.append(rf"(?:{re.escape(self.namespace)}(?::[^:]+)+)")
        return re.compile("|".join(patterns) if patterns else r"(?!)")

    def register(self, client: discord.Client) -> None:
        """Install this router's dispatch item on ``client``, freezing the route table.

        Safe to call for more than one client — a test suite builds a fresh bot per case —
        but not before every route is added, since the template is built here.
        """
        self._registered = True
        client.add_dynamic_items(_dispatch_item(self))

    async def dispatch(
        self,
        interaction: discord.Interaction[Any],
        custom_id: str,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
        values: tuple[str, ...] = (),
    ) -> None:
        """Run the handler owning ``custom_id``; a retired route is logged, never raised."""
        found = self.resolve(custom_id, component=component)
        if found is None:
            if self._in_namespace(custom_id):
                assert self.on_gone is not None
                await self.on_gone(interaction)
                return
            # Reachable when a route is deregistered while its buttons are still posted.
            logger.warning("no route owns custom id %r", custom_id)
            return
        registration, params = found
        accepts = registration.accepts
        wanted = params if accepts is None else {name: params[name] for name in accepts}
        try:
            if component is RouteComponent.SELECT:
                await registration.handler(interaction, values, **wanted)
            else:
                await registration.handler(interaction, **wanted)
        except Exception as error:
            if self.on_error is None:
                logger.exception("routed handler for %r failed", custom_id)
            else:
                await self.on_error(interaction, error, f"route:{custom_id}")

    def _in_namespace(self, custom_id: str) -> bool:
        """Whether ``custom_id`` belongs to this router's reserved namespace."""
        return self.namespace is not None and custom_id.startswith(f"{self.namespace}:")


def _dispatch_item(router: Router[Any]) -> type[discord.ui.DynamicItem[discord.ui.Item[Any]]]:
    """Build the one `DynamicItem` subclass that carries ``router``'s whole route table."""

    class RoutedDispatch(  # pyrefly: ignore[invalid-inheritance]
        discord.ui.DynamicItem[discord.ui.Item[Any]],
        template=router.template(),
    ):
        @classmethod
        @override
        async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
            cls,
            interaction: discord.Interaction[Any],
            item: discord.ui.Item[Any],
            match: re.Match[str],
            /,
        ) -> Self:
            return cls(item)

        @override
        async def callback(self, interaction: discord.Interaction[Any]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
            if isinstance(self.item, discord.ui.Select):
                await router.dispatch(
                    interaction,
                    self.custom_id,
                    component=RouteComponent.SELECT,
                    values=tuple(self.item.values),
                )
                return
            await router.dispatch(interaction, self.custom_id)

    return RoutedDispatch
