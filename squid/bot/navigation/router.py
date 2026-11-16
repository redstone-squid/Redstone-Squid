"""Address screens through the classmethods that open them, then bind those routes in a router.

A screen becomes reachable by decorating a classmethod with `screen_route`. The method receives
the opening request plus the arguments parsed from the address, and returns a fresh screen to
deliver. Names in `{braces}` are path parameters; every other parameter is a query parameter
and needs a default, which `url_for` omits from the address. A route can designate one
optional parameter as its fragment instead of a query parameter. Annotations choose the parsing, so
only `int`, `bool`, `UUID`, string `Literal`s and an optional form of those are supported.

The router owns lookup and delivery only. Audience and deferral come from the screen's own
response policy, so an opening method cannot disagree with it. Discord component dispatch
stays in the bot's control router.
"""

import inspect
from collections.abc import Awaitable, Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Concatenate,
    cast,
    get_type_hints,
    override,
)

import squid_ui.ext.discord as sd
from squid.bot.ui import text_node
from squid_i18n import TextLike
from squid_ui.routing import AddressParts, AddressRoute, RouteTable
from squid_ui.routing import InvalidAddress as InvalidAddress

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


type NavigationRequest = sd.Request[Any, RedstoneSquid]


class ScreenUnavailable(Exception):
    """Raised by an opening method when it has nothing to show.

    The router sends `message` instead of a screen, under the screen's own audience. Use it for
    a missing or inaccessible target as well as for an intentionally empty result.
    """

    def __init__(self, message: TextLike) -> None:
        # No args: a Text has no string form until a localization resolves it.
        super().__init__()
        self.message = message


@dataclass(frozen=True, slots=True)
class ScreenOpening:
    """A fresh screen for the router to deliver."""

    screen: sd.Screen[Any, Any, Any]
    session_key: Hashable | None = None


type Opener[**P] = Callable[Concatenate[Any, NavigationRequest, P], Awaitable[ScreenOpening | None]]
"""A classmethod taking the request, then the route's parameters, as type checkers see it once
`@classmethod` is applied. None means the opener already answered the interaction itself, for
example by starting a consent flow."""


class ScreenRoute[**P]:
    """An opening classmethod together with its address path.

    Reading the attribute from the screen class, as in `BuildBrowseScreen.open_detail`, gives
    this object. Call it like the classmethod, or hand it to `ScreenRouter.url_for` and
    `ScreenRouter.open`, whose arguments are then checked against the method's signature.
    """

    owner: type[sd.Screen[Any, Any, Any]]
    """The screen class that declares this route, set when the class body finishes."""
    name: str

    def __init__(self, path: str, opener: Opener[P], *, fragment: str | None = None) -> None:
        if not isinstance(opener, classmethod):
            msg = "Apply screen_route above @classmethod"
            raise TypeError(msg)
        self.path = path
        self.fragment = fragment
        self._function = opener.__func__

    def __set_name__(self, owner: type[sd.Screen[Any, Any, Any]], name: str) -> None:
        self.owner = owner
        self.name = name

    async def __call__(self, request: NavigationRequest, /, *args: P.args, **kwargs: P.kwargs) -> ScreenOpening | None:
        return await self._function(self.owner, request, *args, **kwargs)

    async def invoke(self, request: NavigationRequest, arguments: Mapping[str, object]) -> ScreenOpening | None:
        """Call the opener with arguments the router has already checked."""
        return await self._function(self.owner, request, **arguments)

    @property
    def signature(self) -> inspect.Signature:
        """The opener's parameters after the screen class and the request."""
        parameters = tuple(inspect.signature(self._function).parameters.values())[2:]
        return inspect.Signature(parameters)

    def hints(self) -> dict[str, Any]:
        return get_type_hints(self._function)

    @property
    def audience(self) -> sd.Audience:
        """The audience of the screen's response policy."""
        # Screen compiles a policy that always sets the audience.
        return cast(sd.Audience, self.owner.__response_spec__.audience)

    @override
    def __repr__(self) -> str:
        return f"{self.owner.__qualname__}.{self.name}"


@dataclass(frozen=True, slots=True)
class _Declare:
    path: str
    fragment: str | None = None

    def __call__[**P](self, opener: Opener[P], /) -> ScreenRoute[P]:
        return ScreenRoute(self.path, opener, fragment=self.fragment)


def screen_route(path: str, /, *, fragment: str | None = None) -> _Declare:
    """Declare the address of a classmethod that opens its screen.

    Apply it above `@classmethod`. A screen class may declare several routes, and a subclass
    does not inherit its parent's routes. Set `fragment` to the name of an optional
    opening parameter to encode it after `#` instead of in the query string.
    """
    return _Declare(path, fragment)


@dataclass(frozen=True, slots=True)
class RouteMatch:
    """The route an address names and the arguments it supplies, defaults included."""

    route: ScreenRoute[...]
    arguments: dict[str, Any]


class ScreenRouter:
    """An explicitly assembled, frozen table of screen routes."""

    def __init__(self) -> None:
        self._bindings: dict[ScreenRoute[...], AddressRoute] = {}
        self._table = RouteTable[ScreenRoute[...]]()
        self._frozen = False

    def register(self, *screens: type[sd.Screen[Any, Any, Any]]) -> None:
        """Bind every route declared on each screen class.

        Only routes declared on the exact class count, and a screen with none is an error.
        """
        if self._frozen:
            msg = "Screen routes are frozen"
            raise RuntimeError(msg)
        for screen in screens:
            routes = [value for value in vars(screen).values() if isinstance(value, ScreenRoute)]
            if not routes:
                msg = f"{screen.__qualname__} declares no screen routes on this exact Screen class"
                raise ValueError(msg)
            for route in routes:
                self._bind(route)

    def _bind(self, route: ScreenRoute[...]) -> None:
        if route in self._bindings:
            msg = "Route already registered"
            raise ValueError(msg)
        address = AddressRoute(route.path, route.signature, route.hints(), fragment=route.fragment)
        try:
            self._table.add(address.pattern, route)
        except ValueError as error:
            msg = "Ambiguous screen route paths"
            raise ValueError(msg) from error
        self._bindings[route] = address

    def freeze(self) -> None:
        self._frozen = True

    def _binding(self, route: ScreenRoute[...]) -> AddressRoute:
        try:
            return self._bindings[route]
        except KeyError as error:
            msg = "Unregistered route"
            raise InvalidAddress(msg) from error

    def parse(self, address: str) -> RouteMatch:
        """Resolve an address, raising InvalidAddress for malformed or unknown destinations."""
        parts = AddressParts.parse(address)
        found = self._table.match(parts.path[1:])
        if found is None:
            msg = "Unknown screen address"
            raise InvalidAddress(msg)
        route, arguments = found
        return RouteMatch(route, self._binding(route).decode(parts, arguments))

    def url_for[**P](self, route: ScreenRoute[P], /, *args: P.args, **kwargs: P.kwargs) -> str:
        """Build the route's address, omitting query parameters left at their defaults."""
        return self._binding(route).build(*args, **kwargs)

    def deferral(self, route: ScreenRoute[...]) -> sd.Deferral:
        """Return the deferral that matches the audience of the route's screen policy.

        A deferral fixes whether the first response is public, so it has to be chosen before the
        screen is built. Only a public screen policy defers publicly.
        """
        return "public" if route.audience == "public" else "private"

    async def open[**P](
        self, request: NavigationRequest, route: ScreenRoute[P], /, *args: P.args, **kwargs: P.kwargs
    ) -> sd.ResponseResult | None:
        """Build the screen with fresh data and deliver it through the request.

        Existing screen session policies still decide whether an older presentation is replaced.
        When the opener raises `ScreenUnavailable`, its message is delivered instead. Returns
        None when the opener already handled the interaction.
        """
        values = self._binding(route).arguments(args, kwargs)
        try:
            opening = await route.invoke(request, values)
        except ScreenUnavailable as unavailable:
            return await request.respond(text_node(unavailable.message), audience=route.audience)
        if opening is None:
            return None
        return await request.respond(opening.screen, session_key=opening.session_key)

    async def open_address(self, request: NavigationRequest, address: str, /) -> sd.ResponseResult | None:
        """Open the route an address names, for callers that cannot import the screen class."""
        match = self.parse(address)
        return await self.open(request, match.route, **match.arguments)
