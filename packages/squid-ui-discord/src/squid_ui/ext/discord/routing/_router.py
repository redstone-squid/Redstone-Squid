"""Own the route table, resolve custom IDs, and coordinate dispatch."""

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import Any, Concatenate

import discord
from squid_ui.ext.discord.adapter import DISCORD_PY_27_ADAPTER, require_discord_py_capability
from squid_ui.ext.discord.capabilities import DiscordAdapterCapability
from squid_ui.ext.discord.messages.contracts import ErrorHook
from squid_ui.ext.discord.target_types import DiscordPyAdapter
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.profiling import NoOpProfiler, Profiler
from squid_ui.routing import Route, RouteTable

from ._client import install
from ._contracts import (
    GoneHook,
    Middleware,
    RouteComponent,
    RouteDescription,
    RouteHandle,
    RouteHandler,
    RouteLike,
    RouteRequest,
    _accepted,
    _Registration,
)
from ._dispatch import execute
from ._groups import RouteGroup

logger = logging.getLogger("squid_ui.ext.discord.routing")
_NOOP_PROFILER = NoOpProfiler()


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
        require_discord_py_capability(adapter, DiscordAdapterCapability.DISPATCH, "dispatch routed interactions")
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
        self._routes: list[RouteHandle[...]] = []
        self._groups: list[RouteGroup[BotT]] = []
        self._middleware: list[Middleware[BotT]] = []
        self._middleware_keys: dict[str, int] = {}
        self._registered = False
        self._tables: dict[RouteComponent, RouteTable[RouteHandle[...]]] = {}
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

    def add_middleware(self, middleware: Middleware[BotT], *, key: str | None = None) -> None:
        """Append middleware, or replace an existing key in its original position.

        Keys allow replacement after registration, including extension reloads. New
        keys must be installed before registration. Without a key, repeated instances
        are ignored by object identity.
        """
        if key is not None and key in self._middleware_keys:
            self._middleware[self._middleware_keys[key]] = middleware
            return
        if key is None and any(existing is middleware for existing in self._middleware):
            return
        if self._registered:
            message = "router middleware must be added before Router.register(client)"
            raise RuntimeError(message)
        if key is not None:
            self._middleware_keys[key] = len(self._middleware)
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
        for handle in self._routes:
            registration = handle._registration
            if registration.route not in routes:
                routes.append(registration.route)
        for group in self._all_groups():
            if group is not excluding:
                routes.extend(group._definitions)
        return tuple(routes)

    def _handles(self) -> tuple[RouteHandle[...], ...]:
        return (*self._routes, *(handle for group in self._all_groups() for handle in group._routes))

    def _registrations(self) -> tuple[_Registration, ...]:
        return tuple(handle._registration for handle in self._handles())

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
        RouteHandle[Concatenate[discord.Interaction[BotT], P]],
    ]:
        """Register ``route``'s handler, as a decorator.

        `Concatenate` pins the first argument to this router's client, so a handler reaches
        `interaction.client` typed instead of re-annotating it. The rest of the signature is
        captured rather than constrained — a checker cannot read parameter names out of a
        format string — which is why `add` re-checks them at import.
        """

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]],
        ) -> RouteHandle[Concatenate[discord.Interaction[BotT], P]]:
            return self.add(route, handler)

        return decorate

    def select[**P](
        self, route: RouteLike
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]]],
        RouteHandle[Concatenate[discord.Interaction[BotT], tuple[str, ...], P]],
    ]:
        """Register a routed string-select handler receiving values before path parameters."""

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]],
        ) -> RouteHandle[Concatenate[discord.Interaction[BotT], tuple[str, ...], P]]:
            return self.add(route, handler, component=RouteComponent.SELECT)

        return decorate

    def add(
        self,
        route: RouteLike,
        handler: RouteHandler,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
    ) -> RouteHandle[...]:
        """Register ``route``'s handler; re-registering the same route replaces it.

        Replacement rather than rejection because loading an extension re-executes its
        module, so a reload registers every route in it a second time with a fresh function
        object. The newest handler wins while the installed template remains unchanged,
        which is why a reload is allowed after `register` while a new route is not.

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
        if isinstance(handler, RouteHandle):
            handler = handler._registration.handler
        registration = _Registration(route, handler, component, _accepted(route, handler, component))
        for group in self._all_groups():
            for defined in group._definitions:
                if route.overlaps(defined):
                    message = f"route {route.format!r} overlaps the included route {defined.format!r}"
                    raise ValueError(message)
        replaced: int | None = None
        for index, handle in enumerate(self._routes):
            existing = handle._registration
            if existing.component is component and existing.route.format == route.format:
                replaced = index
                break
        for index, handle in enumerate(self._routes):
            existing = handle._registration
            if index == replaced:
                continue
            if existing.component is component and route.overlaps(existing.route):
                message = f"route {route.format!r} overlaps the already-registered {existing.route.format!r}"
                raise ValueError(message)
        if replaced is not None:
            handle = self._routes[replaced]
            existing = handle._registration
            if self._registered and existing.route.aliases != route.aliases:
                message = (
                    f"route {route.format!r} cannot change aliases after Router.register(client) builds the template"
                )
                raise RuntimeError(message)
            if existing.route.aliases == route.aliases:
                registration = replace(registration, route=existing.route)
            handle._bind(registration)
            return handle
        if self._registered:
            message = f"new route {route.format!r} must be added before Router.register(client) builds the template"
            raise RuntimeError(message)
        handle = RouteHandle[...](registration)
        self._routes.append(handle)
        return handle

    def resolve(
        self,
        custom_id: str,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
    ) -> tuple[_Registration, Mapping[str, Any]] | None:
        """The registration owning ``custom_id`` and the parameters it carries, if any."""
        table = self._tables.get(component)
        if table is None:
            table = RouteTable[RouteHandle[...]]()
            for handle in self._handles():
                if handle._registration.component is component:
                    table.add(handle.route, handle)
            # Frozen identities can be indexed once. Stable handles still expose
            # replacement handlers after an extension reload.
            if self._registered:
                self._tables[component] = table
        found = table.match(custom_id)
        if found is None:
            return None
        handle, params = found
        return handle._registration, params

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
        """Install dynamic dispatch on a client and freeze route identities and middleware.

        Registering the same router on the same client is idempotent. Existing handlers may
        still be rebound for reloads, but new identities cannot be added after registration.
        Different routers with overlapping IDs are rejected because discord.py dispatches
        every matching dynamic-item template, which would run more than one handler per click.
        """
        install(self, client)

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
        """Resolve a routed control and run its middleware and handler.

        The acknowledgement watchdog defers an unanswered interaction; it does not impose
        a handler execution deadline. Handlers must open modals before that response is spent.
        Authorization and application transactions belong to the installed middleware or
        handler; matching a custom id alone grants neither.
        """
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

        await execute(
            request,
            operation,
            self._middleware_for(request.group_prefix),
            source=source,
            profiler=self.profiler,
            on_error=self.on_error,
            acknowledgement_timeout=self.acknowledgement_timeout,
        )

    def _in_namespace(self, custom_id: str) -> bool:
        """Whether ``custom_id`` belongs to this router's reserved namespace."""
        return self.namespace is not None and custom_id.startswith(f"{self.namespace}:")
