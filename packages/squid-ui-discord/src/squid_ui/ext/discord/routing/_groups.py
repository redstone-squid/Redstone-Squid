"""Build reusable route groups and validate their identities against attached routers."""

import weakref
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Concatenate

import discord
from squid_ui.routing import Route

from ._contracts import Middleware, RouteComponent, RouteHandle, RouteHandler, RouteLike, _accepted, _Registration

if TYPE_CHECKING:
    from ._router import Router


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
        self._routes: list[RouteHandle[...]] = []
        self._middleware: list[Middleware[BotT]] = []
        self._middleware_keys: dict[str, int] = {}
        self._routers: weakref.WeakSet[Router[Any]] = weakref.WeakSet()
        self._frozen = False

    def group(self, *prefix: str) -> RouteGroup[BotT]:
        """Get or create a child group, reusing the same prefix across reloads.

        Existing children remain available after registration; new children do not.
        """
        child = RouteGroup(*prefix, _parent=self)
        for existing in self._children:
            if existing.prefix == child.prefix:
                return existing
        if self._frozen:
            message = f"child groups of {self.prefix!r} must be created before registration"
            raise RuntimeError(message)
        for router in self._routers:
            router._validate_group(child)
            child._attach(router)
        self._children.append(child)
        return child

    def define(self, format: str, *, aliases: tuple[str, ...] = ()) -> Route:
        """Return the existing identity for an identical definition, or define a new route.

        Repeated definitions remain valid after registration so handler modules can reload.
        Changing aliases or defining an overlapping route still fails.
        """
        route = Route(f"{self.prefix}:{format}", aliases=aliases)
        for existing in self._definitions:
            if existing.format == route.format:
                if existing.aliases != route.aliases:
                    message = f"redefined route {route.format!r} changed aliases"
                    raise ValueError(message)
                return existing
        if self._frozen:
            message = f"new route {format!r} must be defined before the route group is registered"
            raise RuntimeError(message)
        for existing in self._definitions:
            if route.overlaps(existing):
                message = f"route {route.format!r} overlaps the group route {existing.format!r}"
                raise ValueError(message)
        for router in self._routers:
            router._validate_group_route(self, route)
        self._definitions.append(route)
        return route

    def route[**P](
        self, route: RouteLike, *, aliases: tuple[str, ...] = ()
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]]],
        RouteHandle[Concatenate[discord.Interaction[BotT], P]],
    ]:
        """Define a group-relative route and return its stable callable handle.

        An existing Route from define() is also accepted. Aliases are absolute custom
        ID formats and may only be supplied with a string definition.
        """
        if isinstance(route, str):
            route = self.define(route, aliases=aliases)
        elif aliases:
            message = "aliases must be supplied with a route format string"
            raise ValueError(message)

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], P], Awaitable[None]],
        ) -> RouteHandle[Concatenate[discord.Interaction[BotT], P]]:
            return self.add(route, handler)

        return decorate

    def select[**P](
        self, route: RouteLike, *, aliases: tuple[str, ...] = ()
    ) -> Callable[
        [Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]]],
        RouteHandle[Concatenate[discord.Interaction[BotT], tuple[str, ...], P]],
    ]:
        """Define a routed select receiving values before path parameters.

        Uses the same identity and reload rules as route().
        """
        if isinstance(route, str):
            route = self.define(route, aliases=aliases)
        elif aliases:
            message = "aliases must be supplied with a route format string"
            raise ValueError(message)

        def decorate(
            handler: Callable[Concatenate[discord.Interaction[BotT], tuple[str, ...], P], Awaitable[None]],
        ) -> RouteHandle[Concatenate[discord.Interaction[BotT], tuple[str, ...], P]]:
            return self.add(route, handler, component=RouteComponent.SELECT)

        return decorate

    def add(
        self,
        route: Route,
        handler: RouteHandler,
        *,
        component: RouteComponent = RouteComponent.BUTTON,
    ) -> RouteHandle[...]:
        """Bind a handler to a route this group defined, replacing it on reload."""
        if route not in self._definitions:
            message = f"route {route.format!r} was not defined by group {self.prefix!r}"
            raise ValueError(message)
        if isinstance(handler, RouteHandle):
            handler = handler._registration.handler
        registration = _Registration(route, handler, component, _accepted(route, handler, component), self.prefix)
        for handle in self._routes:
            existing = handle._registration
            if existing.component is component and existing.route == route:
                handle._bind(registration)
                return handle
        if self._frozen:
            message = f"new {component.value} handler for {route.format!r} must be added before registration"
            raise RuntimeError(message)
        handle = RouteHandle[...](registration)
        self._routes.append(handle)
        return handle

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
        if self._frozen:
            message = f"middleware for route group {self.prefix!r} must be added before registration"
            raise RuntimeError(message)
        if key is not None:
            self._middleware_keys[key] = len(self._middleware)
        self._middleware.append(middleware)

    def _freeze(self) -> None:
        missing = [
            route.format for route in self._definitions if not any(handle.route == route for handle in self._routes)
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
