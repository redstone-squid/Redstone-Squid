"""Dispatch for stateless routed controls.

The counterpart to `MessageRoot` for the tier that outlives the process. A mount owns handlers
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
deliberate asymmetry with `ActionControl`: a routed control's state lives in the message and the
store, so handlers need `interaction.message`, `interaction.guild` and the client — facts
no portable event can carry. The *node* stays portable; only dispatch is Discord's, which
is why this lives here rather than beside the semantic layer.

None of the mount's guarantees apply: no author lock, no generation check, no transaction.
A routed handler owns its own concurrency.

`squid_ui_discord.testing.interaction_harness` plus `Router.dispatch` is this module's
test client — a handler can be exercised without a bot, a gateway or a real message.
"""

import annotationlib
import inspect
import logging
import re
import weakref
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Concatenate, Self, cast, override

import anyio
import discord

from squid_ui.planning.adapter import AdapterCapability, AdapterProfile
from squid_ui.profiling import NoOpProfiler, OperationKind, OperationRecorder, Profiler, TraceResult, TraceStatus
from squid_ui.routing import Route
from squid_ui.target_types import DiscordPyAdapter
from squid_ui.text import NEUTRAL, localization_scope
from squid_ui_discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui_discord.message_root_contracts import ErrorHook

logger = logging.getLogger(__name__)
_NOOP_PROFILER = NoOpProfiler()

type RouteHandler = Callable[..., Awaitable[None]]
"""Storage type. The checked surface is `Router.route`, which pins the first argument."""

type RouteLike = Route | str
"""A route, or the format string to build one from — `Route` is only worth naming when
something outside the handler's module has to build ids from it."""

type GoneHook[BotT: discord.Client] = Callable[[discord.Interaction[BotT]], Awaitable[None]]
"""A friendly response for a control retired from a reserved router namespace."""

type RouteProceed = Callable[[], Awaitable[None]]
"""Proceed through the remaining middleware to the already-resolved handler."""

_INSTALLED: weakref.WeakKeyDictionary[discord.Client, list[Router[Any]]] = weakref.WeakKeyDictionary()
"""Routers installed per client, so `register` can refuse the second router a click would wake."""


def routers[ClientT: discord.Client](client: ClientT) -> tuple[Router[ClientT], ...]:
    """Return a read-only snapshot of the routers installed on ``client``."""
    installed = cast(list[Router[ClientT]], _INSTALLED.get(client, ()))
    return tuple(installed)


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


class RouteGroup[BotT: discord.Client]:
    """A stable route prefix with feature-owned identities and handlers."""

    def __init__(self, *prefix: str, _parent: RouteGroup[BotT] | None = None) -> None:
        if not prefix:
            message = "a route group needs at least one prefix segment"
            raise ValueError(message)
        for segment in prefix:
            parsed = Route(segment)
            if len(parsed.segments) != 1 or parsed.params:
                message = f"route group prefix {segment!r} must be one literal segment"
                raise ValueError(message)
        self._segments = (*(_parent._segments if _parent is not None else ()), *prefix)
        self.prefix = ":".join(self._segments)
        self._parent = _parent
        self._children: list[RouteGroup[BotT]] = []
        self._definitions: list[Route] = []
        self._routes: list[_Registration] = []
        self._middleware: list[Middleware[BotT]] = []
        self._routers: weakref.WeakSet[Router[Any]] = weakref.WeakSet()
        self._frozen = False

    def group(self, *prefix: str) -> RouteGroup[BotT]:
        """Create a child whose final prefix is composed immediately."""
        if self._frozen:
            message = f"child groups of {self.prefix!r} must be created before registration"
            raise RuntimeError(message)
        child = RouteGroup(*prefix, _parent=self)
        for router in self._routers:
            router._validate_group(child)
            child._attach(router)
        self._children.append(child)
        return child

    def define(self, format: str, *, aliases: tuple[str, ...] = ()) -> Route:
        """Define and return one final, context-free route identity."""
        if self._frozen:
            message = f"new route {format!r} must be defined before the route group is registered"
            raise RuntimeError(message)
        route = Route(f"{self.prefix}:{format}", aliases=aliases)
        for existing in self._definitions:
            if route.overlaps(existing):
                message = f"route {route.format!r} overlaps the group route {existing.format!r}"
                raise ValueError(message)
        for router in self._routers:
            router._validate_group_route(self, route)
        self._definitions.append(route)
        return route

    def route[**P](
        self, route: Route
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]]],
        Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]],
    ]:
        """Register a button handler for one identity defined by this group."""

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]],
        ) -> Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]]:
            self.add(route, handler)
            return handler

        return decorate

    def select[**P](
        self, route: Route
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]]],
        Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]],
    ]:
        """Register a select handler for one identity defined by this group."""

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]],
        ) -> Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]]:
            self.add(route, handler, component=RouteComponent.SELECT)
            return handler

        return decorate

    def add(
        self,
        route: Route,
        handler: RouteHandler,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
    ) -> None:
        """Bind a handler to a route this group defined, replacing it on reload."""
        if route not in self._definitions:
            message = f"route {route.format!r} was not defined by group {self.prefix!r}"
            raise ValueError(message)
        registration = _Registration(route, handler, component, _accepted(route, handler, component), self.prefix)
        for index, existing in enumerate(self._routes):
            if existing.component is component and existing.route == route:
                self._routes[index] = registration
                return
        if self._frozen:
            message = f"new {component.value} handler for {route.format!r} must be added before registration"
            raise RuntimeError(message)
        self._routes.append(registration)

    def add_middleware(self, middleware: Middleware[BotT]) -> None:
        """Append one middleware instance, idempotently by object identity."""
        if any(existing is middleware for existing in self._middleware):
            return
        if self._frozen:
            message = f"middleware for route group {self.prefix!r} must be added before registration"
            raise RuntimeError(message)
        self._middleware.append(middleware)

    def _freeze(self) -> None:
        missing = [
            route.format
            for route in self._definitions
            if not any(registration.route == route for registration in self._routes)
        ]
        if missing:
            message = f"route group {self.prefix!r} has identities without handlers: {missing}"
            raise RuntimeError(message)
        self._frozen = True
        for child in self._children:
            child._freeze()

    def _walk(self) -> tuple[RouteGroup[BotT], ...]:
        """This group and every descendant in definition order."""
        return (self, *(group for child in self._children for group in child._walk()))

    def _attach(self, router: Router[Any]) -> None:
        self._routers.add(router)
        for child in self._children:
            child._attach(router)

    def _lineage(self) -> tuple[RouteGroup[BotT], ...]:
        """Ancestors followed by this group, for outer-to-inner middleware order."""
        lineage: list[RouteGroup[BotT]] = []
        current: RouteGroup[BotT] | None = self
        while current is not None:
            lineage.append(current)
            current = current._parent
        lineage.reverse()
        return tuple(lineage)


class Router[BotT: discord.Client]:
    """A table of routes and their handlers, registered once with the client."""

    def __init__(
        self,
        *,
        namespace: str | RouteGroup[BotT] | None = None,
        on_gone: GoneHook[BotT] | None = None,
        on_error: ErrorHook | None = None,
        acknowledgement_timeout: float = 2.5,
        profiler: Profiler = _NOOP_PROFILER,
        adapter: AdapterProfile[DiscordPyAdapter] = DISCORD_PY_27_ADAPTER,
    ) -> None:
        require_discord_py_capability(adapter, AdapterCapability.DISPATCH, "dispatch routed interactions")
        namespace_group = namespace if isinstance(namespace, RouteGroup) else None
        namespace_value = namespace if isinstance(namespace, str) else None
        if namespace_group is not None:
            if len(namespace_group._segments) != 1:
                message = "a router namespace group must have exactly one prefix segment"
                raise ValueError(message)
            namespace_value = namespace_group.prefix
        if namespace_value is not None and (not namespace_value or ":" in namespace_value):
            message = "a router namespace must be one non-empty route segment"
            raise ValueError(message)
        if namespace_value is not None and on_gone is None:
            message = "a namespaced router needs an on_gone hook for retired controls"
            raise ValueError(message)
        if not 0 < acknowledgement_timeout < 3:
            message = "a router acknowledgement timeout must be greater than zero and below Discord's 3-second limit"
            raise ValueError(message)
        self._routes: list[_Registration] = []
        self._groups: list[RouteGroup[BotT]] = []
        self._middleware: list[Middleware[BotT]] = []
        self._registered = False
        self.namespace = namespace_value
        self.on_gone = on_gone
        self.on_error = on_error
        self.acknowledgement_timeout = acknowledgement_timeout
        self.profiler = profiler
        if namespace_group is not None:
            self.include(namespace_group)

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
                group_prefix=registration.group_prefix,
                middleware=tuple(
                    f"{type(middleware).__module__}.{type(middleware).__qualname__}"
                    for middleware in self._middleware_for(registration.group_prefix)
                ),
            )
            for registration in self._registrations()
        )

    def include(self, group: RouteGroup[BotT]) -> None:
        """Include one stable feature group in this router."""
        if group in self._all_groups():
            return
        if self._registered:
            message = f"route group {group.prefix!r} must be included before Router.register(client)"
            raise RuntimeError(message)
        if self.namespace is not None and group._segments[0] != self.namespace:
            message = f"route group {group.prefix!r} does not belong to router namespace {self.namespace!r}"
            raise ValueError(message)
        pending: list[Route] = []
        existing = self._defined_routes()
        for candidate_group in group._walk():
            for route in candidate_group._definitions:
                collision = next((other for other in (*existing, *pending) if route.overlaps(other)), None)
                if collision is not None:
                    message = f"route {route.format!r} overlaps the included route {collision.format!r}"
                    raise ValueError(message)
                pending.append(route)
        self._groups.append(group)
        group._attach(self)

    def add_middleware(self, middleware: Middleware[BotT]) -> None:
        """Append one router-wide middleware instance, idempotently by identity."""
        if any(existing is middleware for existing in self._middleware):
            return
        if self._registered:
            message = "router middleware must be added before Router.register(client)"
            raise RuntimeError(message)
        self._middleware.append(middleware)

    def _validate_group(self, group: RouteGroup[Any]) -> None:
        """Validate a child added below an already-included group."""
        if self._registered:
            message = f"route group {group.prefix!r} must be created before Router.register(client)"
            raise RuntimeError(message)
        for route in group._definitions:
            self._validate_group_route(group, route)

    def _validate_group_route(self, group: RouteGroup[Any], route: Route) -> None:
        """Reject a group identity that intersects anything this router already owns."""
        if self._registered:
            message = f"new route {route.format!r} must be defined before Router.register(client)"
            raise RuntimeError(message)
        for existing in self._defined_routes(excluding=group):
            if route.overlaps(existing):
                message = f"route {route.format!r} overlaps the included route {existing.format!r}"
                raise ValueError(message)

    def _defined_routes(self, *, excluding: RouteGroup[Any] | None = None) -> tuple[Route, ...]:
        routes: list[Route] = []
        for registration in self._routes:
            if registration.route not in routes:
                routes.append(registration.route)
        for group in self._all_groups():
            if group is not excluding:
                routes.extend(group._definitions)
        return tuple(routes)

    def _registrations(self) -> tuple[_Registration, ...]:
        return (*self._routes, *(registration for group in self._all_groups() for registration in group._routes))

    def _all_groups(self) -> tuple[RouteGroup[BotT], ...]:
        seen: list[RouteGroup[BotT]] = []
        for root in self._groups:
            for group in root._walk():
                if group not in seen:
                    seen.append(group)
        return tuple(seen)

    def _middleware_for(self, group_prefix: str | None) -> tuple[Middleware[BotT], ...]:
        """Router middleware followed by the matched group's inherited middleware."""
        if group_prefix is None:
            return tuple(self._middleware)
        group = next(group for group in self._all_groups() if group.prefix == group_prefix)
        return (*self._middleware, *(middleware for member in group._lineage() for middleware in member._middleware))

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
        for group in self._all_groups():
            for defined in group._definitions:
                if route.overlaps(defined):
                    message = f"route {route.format!r} overlaps the included route {defined.format!r}"
                    raise ValueError(message)
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
        for registration in self._registrations():
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
        registrations = self._registrations()
        if not registrations:
            # An alternation of nothing matches everything; match nothing instead.
            patterns: list[str] = []
        else:
            patterns = [f"(?:{registration.route.anonymous})" for registration in registrations]
        if self.namespace is not None:
            patterns.append(rf"(?:{re.escape(self.namespace)}(?::[^:]+)+)")
        return re.compile("|".join(patterns) if patterns else r"(?!)")

    def register(self, client: discord.Client) -> None:
        """Install this router's dispatch item on ``client``, freezing the route table.

        Safe to call for more than one client — a test suite builds a fresh bot per case —
        but not before every route is added, since the template is built here. Registering
        the same router on the same client again is a no-op: discord.py's `ViewStore`
        schedules one call per matching dynamic item, so a second install would dispatch
        every click twice. A *different* router is rejected when any custom id could reach
        both, with the same exact-intersection check `add()` uses, because the losing
        router would answer ids it does not own with a warning or its gone hook.
        """
        for group in self._groups:
            group._freeze()
        installed = _INSTALLED.setdefault(client, [])
        if self in installed:
            return
        for other in installed:
            collision = self._collision(other)
            if collision is not None:
                message = f"client already has a router this one collides with: {collision}"
                raise ValueError(message)
        self._registered = True
        installed.append(self)
        client.add_dynamic_items(_dispatch_item(self))

    def _collision(self, other: Router[Any]) -> str | None:
        """Why this router's accepted ids intersect ``other``'s, or None if disjoint.

        Component kinds are ignored on purpose: templates match custom ids, not component
        kinds, so a button route in one router still wakes another router's dispatch item
        when the id languages intersect.
        """
        if self.namespace is not None and self.namespace == other.namespace:
            return f"both reserve the namespace {self.namespace!r}"
        for registration in self._registrations():
            if other.namespace is not None and registration.route.accepts_first_segment(other.namespace):
                return f"route {registration.route.format!r} enters the reserved namespace {other.namespace!r}"
        for registration in other._registrations():
            if self.namespace is not None and registration.route.accepts_first_segment(self.namespace):
                return f"route {registration.route.format!r} enters the reserved namespace {self.namespace!r}"
        for mine in self._registrations():
            for theirs in other._registrations():
                if mine.route.overlaps(theirs.route):
                    return f"route {mine.route.format!r} overlaps {theirs.route.format!r}"
        return None

    async def dispatch(
        self,
        interaction: discord.Interaction[Any],
        custom_id: str,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
        values: tuple[str, ...] = (),
    ) -> None:
        """Run the handler owning ``custom_id`` under the acknowledgement deadline."""
        found = self.resolve(custom_id, component=component)
        registration: _Registration | None = None
        params: Mapping[str, Any] = MappingProxyType({})
        if found is None:
            if self._in_namespace(custom_id):
                gone = self.on_gone
                assert gone is not None

                async def operation() -> None:
                    await gone(interaction)

                source = f"route-gone:{self.namespace}"
            else:
                # Reachable only through direct dispatch after a route was deregistered;
                # the installed DynamicItem template cannot admit an unrelated id.
                async def unknown() -> None:
                    logger.warning("no route owns custom id %r", custom_id)

                operation = unknown
                source = "route:unknown"
        else:
            registration, params = found
            accepts = registration.accepts
            wanted = params if accepts is None else {name: params[name] for name in accepts}

            async def operation() -> None:
                if component is RouteComponent.SELECT:
                    await registration.handler(interaction, values, **wanted)
                else:
                    await registration.handler(interaction, **wanted)

            source = f"route:{registration.route.format}"

        request = RouteRequest(
            interaction=interaction,
            component=component,
            route_id=custom_id,
            route=None if registration is None else registration.route,
            params=MappingProxyType(dict(params)),
            values=values,
            group_prefix=None if registration is None else registration.group_prefix,
            matched_alias=(registration is not None and registration.route.pattern.fullmatch(custom_id) is None),
        )

        middleware = self._middleware_for(request.group_prefix)
        trace_name = registration.route.format if registration is not None else source
        with self.profiler.operation(
            OperationKind.ROUTE_DISPATCH,
            name=trace_name,
            attributes={
                "component": component.value,
                "matched_alias": request.matched_alias,
                "middleware": len(middleware),
                "actor": interaction.user.id,
            },
        ) as profile:
            acknowledgement = profile.start_span("acknowledgement")

            def finish_acknowledgement(source: str) -> None:
                if interaction.response.is_done():
                    acknowledgement.set_attribute("source", source)
                    acknowledgement.finish()

            async def endpoint() -> None:
                with profile.span("handler"):
                    await operation()
                finish_acknowledgement("handler")

            async def dispatch_operation() -> None:
                from squid_ui_discord.request import request as resolve_request
                from squid_ui_discord.runtime import DiscordUIRuntimeMissing

                try:
                    localization = (await resolve_request(interaction)).localization
                except DiscordUIRuntimeMissing:
                    localization = NEUTRAL
                try:
                    with localization_scope(localization):
                        handled = await self._run_middleware(middleware, request, endpoint, profile=profile)
                except Exception as error:
                    profile.set_result(
                        TraceResult(TraceStatus.FAILED, f"{type(error).__module__}.{type(error).__qualname__}")
                    )
                    with localization_scope(localization), profile.span("error_hook"):
                        await self._handle_error(interaction, error, source)
                    finish_acknowledgement("error_hook")
                else:
                    profile.set_result(TraceResult(TraceStatus.COMPLETED, None if handled else "short_circuited"))

            async def watchdog() -> None:
                await anyio.sleep(self.acknowledgement_timeout)
                deferred = await self._acknowledge_safely(interaction, source)
                finish_acknowledgement("watchdog" if deferred else "handler")
                if deferred:
                    profile.mark_deadline_missed()

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(watchdog)
                try:
                    await dispatch_operation()
                finally:
                    tasks.cancel_scope.cancel()
            deferred = await self._acknowledge_safely(interaction, source)
            finish_acknowledgement("final_defer" if deferred else "handler")

    async def _run_middleware(
        self,
        middleware: tuple[Middleware[BotT], ...],
        request: RouteRequest[BotT],
        endpoint: RouteProceed,
        *,
        profile: OperationRecorder,
    ) -> bool:
        """Compose a one-shot middleware chain in first-added, outermost order."""
        handled = False

        async def invoke(index: int) -> None:
            nonlocal handled
            if index == len(middleware):
                handled = True
                await endpoint()
                return

            active = True
            called = False

            async def proceed() -> None:
                nonlocal called
                if not active:
                    message = "route middleware proceed() is only valid during dispatch()"
                    raise RuntimeError(message)
                if called:
                    message = "route middleware proceed() may only be called once"
                    raise RuntimeError(message)
                called = True
                await invoke(index + 1)

            try:
                kind = type(middleware[index])
                with profile.span(f"middleware:{kind.__module__}.{kind.__qualname__}"):
                    await middleware[index].dispatch(request, proceed)
            finally:
                active = False

        await invoke(0)
        return handled

    async def _handle_error(
        self,
        interaction: discord.Interaction[Any],
        error: Exception,
        source: str,
    ) -> None:
        """Present one dispatch failure without allowing the error hook to escape."""
        if self.on_error is None:
            logger.error("routed handler failed in %s", source, exc_info=error)
            return
        try:
            await self.on_error(interaction, error, source)
        except Exception:
            logger.exception("routed error hook failed in %s", source)

    async def _acknowledge_safely(self, interaction: discord.Interaction[Any], source: str) -> bool:
        """Acknowledge an unused response slot, tolerating a concurrent handler response."""
        if interaction.response.is_done():
            return False
        try:
            await interaction.response.defer()
        except discord.InteractionResponded:
            return False
        except Exception:
            logger.exception("could not acknowledge %s", source)
            return False
        return True

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
