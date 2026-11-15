"""Declare screen addresses without global registration, then bind their factories.

The router constructs fresh content under the caller's request scope. Only ``open``
performs delivery; factories may return None after an existing consent flow handles
an interaction. Discord component dispatch remains in the bot's control router.
"""

import re
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import MISSING, dataclass, fields
from types import UnionType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAliasType, cast, get_args, get_origin, get_type_hints
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

import squid_ui.ext.discord as sd
from squid.bot.navigation.destinations import Destination

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


type NavigationRequest = sd.Request[Any, RedstoneSquid]


class InvalidAddress(ValueError):
    """An address or typed destination does not match the registered routes."""


@dataclass(frozen=True, slots=True)
class ScreenOpening:
    response: sd.Response[Any]
    session_key: Hashable | None = None


type ScreenFactory[D: Destination] = Callable[[NavigationRequest, D], Awaitable[ScreenOpening | None]]


@dataclass(frozen=True, slots=True)
class ScreenRoute[D: Destination]:
    path: str
    destination: type[D]
    query: tuple[str, ...]


class ScreenDecorator(Protocol):
    def __call__[S: sd.Screen[Any, Any, Any]](self, screen: type[S], /) -> type[S]: ...


def screen_route[D: Destination](path: str, *, destination: type[D], query: tuple[str, ...] = ()) -> ScreenDecorator:
    """Attach a declaration to this exact Screen class without registering a handler."""

    def decorate[S: sd.Screen[Any, Any, Any]](screen: type[S]) -> type[S]:
        declarations = screen.__dict__.get("__screen_routes__", ())
        cast(Any, screen).__screen_routes__ = *declarations, ScreenRoute(path, destination, query)
        return screen

    return decorate


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
class _Binding:
    declaration: ScreenRoute[Any]
    segments: tuple[str, ...]
    parameters: tuple[str, ...]
    annotations: dict[str, Any]
    defaults: dict[str, object]
    factory: ScreenFactory[Any]


class ScreenRouter:
    """An explicitly assembled, frozen table of screen destinations and factories."""

    def __init__(self) -> None:
        self._bindings: dict[type[Destination], _Binding] = {}
        self._frozen = False

    def register[D: Destination](
        self, screen: type[sd.Screen[Any, Any, Any]], *, destination: type[D], factory: ScreenFactory[D]
    ) -> None:
        if self._frozen:
            msg = "Screen routes are frozen"
            raise RuntimeError(msg)
        if destination in self._bindings:
            msg = "Destination already registered"
            raise ValueError(msg)
        declarations = [
            declaration
            for declaration in screen.__dict__.get("__screen_routes__", ())
            if declaration.destination is destination
        ]
        if len(declarations) != 1:
            msg = "Expected one declaration on this exact Screen class for the destination"
            raise ValueError(msg)
        declaration = declarations[0]
        if not re.fullmatch(r"/(?:[a-z][a-z0-9-]*|\{[a-z_]+\})(?:/(?:[a-z][a-z0-9-]*|\{[a-z_]+\}))*", declaration.path):
            msg = "Invalid route path"
            raise ValueError(msg)
        segments = tuple(declaration.path[1:].split("/"))
        parameters = tuple(segment[1:-1] for segment in segments if segment.startswith("{"))
        names = (*parameters, *declaration.query)
        annotations = get_type_hints(destination)
        declared_fields = fields(destination)
        if len(set(names)) != len(names) or set(names) != {field.name for field in declared_fields}:
            msg = "Every destination field must occur exactly once in the path or query"
            raise ValueError(msg)
        if not all(_supported(annotations[name]) for name in names):
            msg = "Unsupported destination field type"
            raise ValueError(msg)
        defaults: dict[str, object] = {}
        for field in declared_fields:
            if field.default_factory is not MISSING:
                msg = "Route defaults must be constant values"
                raise ValueError(msg)
            if field.name in parameters:
                if field.default is not MISSING or type(None) in get_args(annotations[field.name]):
                    msg = "Path parameters must be required"
                    raise ValueError(msg)
            elif field.default is MISSING:
                msg = "Query parameters must have defaults"
                raise ValueError(msg)
            else:
                defaults[field.name] = field.default
        for previous in self._bindings.values():
            if len(segments) == len(previous.segments) and all(
                left == right or left.startswith("{") or right.startswith("{")
                for left, right in zip(segments, previous.segments, strict=True)
            ):
                msg = "Ambiguous screen route paths"
                raise ValueError(msg)
        self._bindings[destination] = _Binding(
            declaration, segments, parameters, annotations, defaults, cast(ScreenFactory[Any], factory)
        )

    def freeze(self) -> None:
        self._frozen = True

    def parse(self, address: str) -> Destination:
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
        for binding in self._bindings.values():
            if len(segments) != len(binding.segments):
                continue
            if any(
                expected != actual and not expected.startswith("{")
                for expected, actual in zip(binding.segments, segments, strict=True)
            ):
                continue
            if query.keys() - set(binding.declaration.query):
                msg = "Unknown query parameter"
                raise InvalidAddress(msg)
            values = dict(binding.defaults)
            for expected, actual in zip(binding.segments, segments, strict=True):
                if expected.startswith("{"):
                    name = expected[1:-1]
                    values[name] = _decode(actual, binding.annotations[name])
            for name, value in query.items():
                values[name] = _decode(value, binding.annotations[name])
            return binding.declaration.destination(**values)
        msg = "Unknown screen address"
        raise InvalidAddress(msg)

    def format(self, destination: Destination) -> str:
        binding = self._bindings.get(type(destination))
        if binding is None:
            msg = "Unregistered destination"
            raise InvalidAddress(msg)
        values = {field.name: getattr(destination, field.name) for field in fields(destination)}
        for name, value in values.items():
            if value is None and type(None) in get_args(binding.annotations[name]):
                continue
            decoded = _decode(_encode(value), binding.annotations[name])
            if decoded != value:
                msg = "Invalid typed destination"
                raise InvalidAddress(msg)
        path = binding.declaration.path.format(**{name: _encode(value) for name, value in values.items()})
        query = urlencode(
            [
                (name, _encode(values[name]))
                for name in binding.declaration.query
                if values[name] != binding.defaults[name]
            ]
        )
        return f"{path}?{query}" if query else path

    async def resolve(self, request: NavigationRequest, destination: Destination) -> ScreenOpening | None:
        self.format(destination)
        return await self._bindings[type(destination)].factory(request, destination)

    async def open(self, request: NavigationRequest, destination: Destination) -> sd.ResponseResult | None:
        opening = await self.resolve(request, destination)
        if opening is None:
            return None
        return await request.respond(opening.response, session_key=opening.session_key)
