"""Address screens through the classmethods that open them, then bind those routes in a router.

A screen becomes reachable by decorating a classmethod with `screen_route`. The method receives
the opening request plus the arguments parsed from the address, and returns a fresh screen to
deliver. Names in `{braces}` are path parameters; every other parameter is a query parameter
and needs a default, which `url_for` omits from the address. Annotations choose the parsing, so
only `int`, `bool`, `UUID`, string `Literal`s and an optional form of those are supported.

The router owns lookup and delivery only. Audience and deferral come from the screen's own
response policy, so an opening method cannot disagree with it. Discord component dispatch
stays in the bot's control router.
"""

import inspect
import re
from collections.abc import Awaitable, Callable, Hashable, Mapping
from dataclasses import dataclass
from types import UnionType
from typing import (
    TYPE_CHECKING,
    Any,
    Concatenate,
    Literal,
    TypeAliasType,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    override,
)
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

import squid_ui.ext.discord as sd
from squid.bot.ui import text_node
from squid_i18n import TextLike

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


type NavigationRequest = sd.Request[Any, RedstoneSquid]


class InvalidAddress(ValueError):
    """An address or route arguments do not match a registered route."""


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

    def __init__(self, path: str, opener: Opener[P]) -> None:
        if not isinstance(opener, classmethod):
            msg = "Apply screen_route above @classmethod"
            raise TypeError(msg)
        self.path = path
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

    def __call__[**P](self, opener: Opener[P], /) -> ScreenRoute[P]:
        return ScreenRoute(self.path, opener)


def screen_route(path: str, /) -> _Declare:
    """Declare the address of a classmethod that opens its screen.

    Apply it above `@classmethod`. A screen class may declare several routes, and a subclass
    does not inherit its parent's routes.
    """
    return _Declare(path)


def _supported(annotation: object) -> bool:
    if isinstance(annotation, TypeAliasType):
        return _supported(annotation.__value__)
    if annotation in (int, bool, UUID):
        return True
    if get_origin(annotation) is Literal:
        return all(isinstance(value, str) for value in get_args(annotation))
    if get_origin(annotation) is UnionType:
        arguments = get_args(annotation)
        return (
            len(arguments) == 2
            and type(None) in arguments
            and all(value is type(None) or _supported(value) for value in arguments)
        )
    return False


def _decode(value: str, annotation: object) -> object:
    if isinstance(annotation, TypeAliasType):
        return _decode(value, annotation.__value__)
    if get_origin(annotation) is UnionType:
        return _decode(value, next(argument for argument in get_args(annotation) if argument is not type(None)))
    if annotation is int:
        if not re.fullmatch(r"[0-9]+", value) or int(value) <= 0:
            msg = "IDs must be positive integers"
            raise InvalidAddress(msg)
        return int(value)
    if annotation is UUID:
        try:
            return UUID(value)
        except ValueError as error:
            msg = "Invalid UUID"
            raise InvalidAddress(msg) from error
    if annotation is bool and value in ("true", "false"):
        return value == "true"
    if get_origin(annotation) is Literal and value in get_args(annotation):
        return value
    msg = "Invalid parameter value"
    raise InvalidAddress(msg)


def _encode(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


@dataclass(frozen=True, slots=True)
class RouteMatch:
    """The route an address names and the arguments it supplies, defaults included."""

    route: ScreenRoute[...]
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Binding:
    route: ScreenRoute[...]
    segments: tuple[str, ...]
    parameters: tuple[str, ...]
    query: tuple[str, ...]
    annotations: dict[str, Any]
    defaults: dict[str, object]


class ScreenRouter:
    """An explicitly assembled, frozen table of screen routes."""

    def __init__(self) -> None:
        self._bindings: list[_Binding] = []
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
        if any(binding.route is route for binding in self._bindings):
            msg = "Route already registered"
            raise ValueError(msg)
        if not re.fullmatch(r"/(?:[a-z][a-z0-9-]*|\{[a-z_]+\})(?:/(?:[a-z][a-z0-9-]*|\{[a-z_]+\}))*", route.path):
            msg = "Invalid route path"
            raise ValueError(msg)
        segments = tuple(route.path[1:].split("/"))
        parameters = tuple(segment[1:-1] for segment in segments if segment.startswith("{"))
        declared = route.signature.parameters
        annotations = route.hints()
        if len(set(parameters)) != len(parameters) or not set(parameters) <= declared.keys():
            msg = "Every path parameter must be a parameter of the opening method, exactly once"
            raise ValueError(msg)
        defaults: dict[str, object] = {}
        for name, parameter in declared.items():
            if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
                msg = "Route parameters must be named, without *args or **kwargs"
                raise ValueError(msg)
            if not _supported(annotations.get(name)):
                msg = "Unsupported route parameter type"
                raise ValueError(msg)
            if name in parameters:
                if parameter.default is not parameter.empty or type(None) in get_args(annotations[name]):
                    msg = "Path parameters must be required"
                    raise ValueError(msg)
            elif parameter.default is parameter.empty:
                msg = "Query parameters must have defaults"
                raise ValueError(msg)
            else:
                defaults[name] = parameter.default
        for previous in self._bindings:
            if len(segments) == len(previous.segments) and all(
                left == right or left.startswith("{") or right.startswith("{")
                for left, right in zip(segments, previous.segments, strict=True)
            ):
                msg = "Ambiguous screen route paths"
                raise ValueError(msg)
        query = tuple(name for name in declared if name not in parameters)
        self._bindings.append(_Binding(route, segments, parameters, query, annotations, defaults))

    def freeze(self) -> None:
        self._frozen = True

    def _binding(self, route: ScreenRoute[...]) -> _Binding:
        for binding in self._bindings:
            if binding.route is route:
                return binding
        msg = "Unregistered route"
        raise InvalidAddress(msg)

    def parse(self, address: str) -> RouteMatch:
        """Match an address to its route.

        Raises:
            InvalidAddress: The address is malformed, names no route, or has a bad argument.
        """
        if any(character.isspace() for character in address) or "#" in address or "%" in address:
            msg = "Expected a relative screen address"
            raise InvalidAddress(msg)
        if not address.startswith("/") or address.startswith("//"):
            message = "Expected a relative screen address"
            raise InvalidAddress(message)
        parsed = urlsplit(address)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            msg = "Expected a relative screen address"
            raise InvalidAddress(msg)
        segments = tuple(parsed.path[1:].split("/"))
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as error:
            msg = "Invalid query parameters"
            raise InvalidAddress(msg) from error
        query = dict(pairs)
        if len(query) != len(pairs):
            msg = "Duplicate query parameter"
            raise InvalidAddress(msg)
        for binding in self._bindings:
            if len(segments) != len(binding.segments):
                continue
            if any(
                expected != actual and not expected.startswith("{")
                for expected, actual in zip(binding.segments, segments, strict=True)
            ):
                continue
            if query.keys() - set(binding.query):
                msg = "Unknown query parameter"
                raise InvalidAddress(msg)
            values = dict(binding.defaults)
            for expected, actual in zip(binding.segments, segments, strict=True):
                if expected.startswith("{"):
                    name = expected[1:-1]
                    values[name] = _decode(actual, binding.annotations[name])
            for name, value in query.items():
                values[name] = _decode(value, binding.annotations[name])
            return RouteMatch(binding.route, values)
        msg = "Unknown screen address"
        raise InvalidAddress(msg)

    def _arguments(self, binding: _Binding, args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, object]:
        """Bind and check the arguments for a route, so only addressable values reach the opener."""
        bound = binding.route.signature.bind(*args, **kwargs)
        bound.apply_defaults()
        for name, value in bound.arguments.items():
            if value is None and type(None) in get_args(binding.annotations[name]):
                continue
            if _decode(_encode(value), binding.annotations[name]) != value:
                msg = "Invalid route argument"
                raise InvalidAddress(msg)
        return dict(bound.arguments)

    def url_for[**P](self, route: ScreenRoute[P], /, *args: P.args, **kwargs: P.kwargs) -> str:
        """Return the address of a route, omitting query parameters left at their defaults.

        Raises:
            InvalidAddress: The route is not registered or an argument cannot be addressed.
        """
        binding = self._binding(route)
        values = self._arguments(binding, args, kwargs)
        path = route.path.format(**{name: _encode(values[name]) for name in binding.parameters})
        query = urlencode(
            [(name, _encode(values[name])) for name in binding.query if values[name] != binding.defaults[name]]
        )
        return f"{path}?{query}" if query else path

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
        values = self._arguments(self._binding(route), args, kwargs)
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
