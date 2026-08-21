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


@dataclass(frozen=True, slots=True)
class _Registration:
    route: Route
    handler: RouteHandler
    accepts: frozenset[str] | None
    """Parameter names to pass, or None for a handler taking `**kwargs`."""


def _sample(route: Route) -> str:
    """One id standing in for every id ``route`` can build, for the overlap check."""
    return route.id(**{name: converter.sample for name, converter in zip(route.params, route.converters, strict=True)})


def _accepted(route: Route, handler: RouteHandler) -> frozenset[str] | None:
    """Which of ``route``'s parameters ``handler`` asked for, rejecting names it invented."""
    # FORWARDREF, because evaluating a handler's annotations here would resurrect the
    # TYPE_CHECKING-only client import that PEP 649 exists to keep deferred. Only names and
    # kinds are wanted anyway.
    signature = inspect.signature(handler, annotation_format=annotationlib.Format.FORWARDREF)
    parameters = list(signature.parameters.values())
    if not parameters:
        message = f"route handler {handler.__qualname__!r} must take the interaction as its first argument"
        raise ValueError(message)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        return None
    named = {
        parameter.name
        for parameter in parameters[1:]
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

    def __init__(self, *, on_error: ErrorHook | None = None) -> None:
        self._routes: list[_Registration] = []
        self._registered = False
        self.on_error = on_error

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

    def add(self, route: RouteLike, handler: RouteHandler) -> None:
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
        intersection in general. Resolution is first-match-wins in registration order, so an
        overlap this misses shadows a later route rather than dispatching a click twice.
        """
        route = Route(route) if isinstance(route, str) else route
        registration = _Registration(route, handler, _accepted(route, handler))
        for index, existing in enumerate(self._routes):
            if existing.route.format == route.format:
                self._routes[index] = registration
                return
        sample = _sample(route)
        for existing in self._routes:
            other = _sample(existing.route)
            if existing.route.pattern.fullmatch(sample) or route.pattern.fullmatch(other):
                message = f"route {route.format!r} overlaps the already-registered {existing.route.format!r}"
                raise ValueError(message)
        if self._registered:
            message = f"new route {route.format!r} must be added before Router.register(client) builds the template"
            raise RuntimeError(message)
        self._routes.append(registration)

    def resolve(self, custom_id: str) -> tuple[_Registration, Mapping[str, Any]] | None:
        """The registration owning ``custom_id`` and the parameters it carries, if any."""
        for registration in self._routes:
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
            return re.compile(r"(?!)")
        return re.compile("|".join(f"(?:{registration.route.anonymous})" for registration in self._routes))

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
        registration, params = found
        accepts = registration.accepts
        wanted = params if accepts is None else {name: params[name] for name in accepts}
        try:
            await registration.handler(interaction, **wanted)
        except Exception as error:
            if self.on_error is None:
                logger.exception("routed handler for %r failed", custom_id)
            else:
                await self.on_error(interaction, error, f"route:{custom_id}")


def _dispatch_item(router: Router[Any]) -> type[discord.ui.DynamicItem[discord.ui.Button[Any]]]:
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
