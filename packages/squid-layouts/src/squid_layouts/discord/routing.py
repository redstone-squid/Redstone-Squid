"""Dispatch for stateless routed controls.

The counterpart to `Mount` for the tier that outlives the process. A mount owns handlers
in memory and re-issues ids per generation; a router owns a table of `Route`s and resolves
an incoming custom id against it, so a button drawn a year ago by a since-restarted
process still reaches code.

Handlers take the raw `discord.Interaction`. That is a deliberate asymmetry with `Action`:
a routed control's state lives in the message and the store, so handlers need
`interaction.message`, `interaction.guild` and the client — facts no portable event can
carry. The *node* stays portable; only dispatch is Discord's, which is why this lives here
rather than beside the semantic layer.

None of the mount's guarantees apply: no author lock, no generation check, no transaction.
A routed handler owns its own concurrency.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Self, override

import discord

from squid_layouts.discord.mount import ErrorHook
from squid_layouts.routing import Route

logger = logging.getLogger(__name__)

type RouteHandler = Callable[[discord.Interaction[Any], Mapping[str, Any]], Awaitable[None]]


def _sample(route: Route) -> str:
    """One id standing in for every id ``route`` can build, for the overlap check."""
    return route.id(**{name: converter.sample for name, converter in zip(route.params, route.converters, strict=True)})


class Router:
    """A table of routes and their handlers, registered once with the client."""

    def __init__(self, *, on_error: ErrorHook | None = None) -> None:
        self._routes: list[tuple[Route, RouteHandler]] = []
        self._registered = False
        self.on_error = on_error

    def route(self, route: Route) -> Callable[[RouteHandler], RouteHandler]:
        """Register ``route``'s handler, as a decorator."""

        def decorate(handler: RouteHandler) -> RouteHandler:
            self.add(route, handler)
            return handler

        return decorate

    def add(self, route: Route, handler: RouteHandler) -> None:
        """Register ``route``'s handler; re-registering the same route replaces it.

        Replacement rather than rejection because loading an extension re-executes its
        module, so a reload registers every route in it a second time with a fresh function
        object. The newest handler wins, exactly as it did when these were `DynamicItem`
        classes keyed by template. It also leaves the template unchanged, which is why a
        reload is allowed after `register` while a genuinely new route is not.

        A route that *shadows* another is still rejected. That check substitutes each
        converter's probe value for every parameter and tries one route's sample id against
        the other's pattern, which catches the shapes that occur in practice (a literal id
        under a parameterized one, and the reverse) without claiming to decide regex
        intersection in general. Resolution is first-match-wins in registration order, so an overlap this
        misses shadows a later route rather than dispatching a click twice.
        """
        for index, (existing, _handler) in enumerate(self._routes):
            if existing.format == route.format:
                self._routes[index] = (route, handler)
                return
        sample = _sample(route)
        for existing, _handler in self._routes:
            other = _sample(existing)
            if existing.pattern.fullmatch(sample) or route.pattern.fullmatch(other):
                message = f"route {route.format!r} overlaps the already-registered {existing.format!r}"
                raise ValueError(message)
        if self._registered:
            message = f"new route {route.format!r} must be added before Router.register(client) builds the template"
            raise RuntimeError(message)
        self._routes.append((route, handler))

    def resolve(self, custom_id: str) -> tuple[RouteHandler, dict[str, str]] | None:
        """The handler for ``custom_id`` and the parameters it carries, if any route owns it."""
        for route, handler in self._routes:
            params = route.match(custom_id)
            if params is not None:
                return handler, params
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
            return re.compile(r"(?!)")
        return re.compile("|".join(f"(?:{route.anonymous})" for route, _handler in self._routes))

    def register(self, client: discord.Client) -> None:
        """Install this router's dispatch item on ``client``, freezing the route table.

        Safe to call for more than one client — a test suite builds a fresh bot per case —
        but not before every route is added, since the template is built here.
        """
        self._registered = True
        client.add_dynamic_items(_dispatch_item(self))

    async def dispatch(self, interaction: discord.Interaction[Any], custom_id: str) -> None:
        """Run the handler owning ``custom_id``; a retired route is logged, never raised."""
        found = self.resolve(custom_id)
        if found is None:
            # Reachable when a route is deregistered while its buttons are still posted.
            logger.warning("no route owns custom id %r", custom_id)
            return
        handler, params = found
        try:
            await handler(interaction, params)
        except Exception as error:
            if self.on_error is None:
                logger.exception("routed handler for %r failed", custom_id)
            else:
                await self.on_error(interaction, error, f"route:{custom_id}")


def _dispatch_item(router: Router) -> type[discord.ui.DynamicItem[discord.ui.Button[Any]]]:
    """Build the one `DynamicItem` subclass that carries ``router``'s whole route table."""

    class RoutedDispatch(  # pyrefly: ignore[invalid-inheritance]
        discord.ui.DynamicItem[discord.ui.Button[Any]],
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
            # The wrapped button is a dispatch stub: discord.py swaps it into a view rebuilt
            # from the message, and only its custom id is ever read.
            return cls(discord.ui.Button(label="\N{ZERO WIDTH SPACE}", custom_id=match.string))

        @override
        async def callback(self, interaction: discord.Interaction[Any]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
            await router.dispatch(interaction, self.custom_id)

    return RoutedDispatch
