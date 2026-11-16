"""Typed relative addresses layered over the shared route pattern engine.

A callable signature declares required path parameters and optional query parameters.
One optional parameter can be encoded as a fragment. This layer validates addresses
without importing screens, Discord, or an HTTP framework. Integer parameters are
positive IDs; controls can choose different converters for counters or other state.
"""

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from types import UnionType
from typing import Any, Literal, TypeAliasType, get_args, get_origin
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit
from uuid import UUID

from squid_ui.routing.pattern import Converter, RoutePattern


class InvalidAddress(ValueError):
    """A relative address or its arguments do not match the route contract."""


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
class AddressParts:
    """A validated relative address before a route interprets its parameter values."""

    path: str
    query: Mapping[str, str]
    fragment: str | None

    @classmethod
    def parse(cls, address: str) -> AddressParts:
        if any(character.isspace() for character in address) or "%" in address.partition("#")[0]:
            msg = "Expected a relative address"
            raise InvalidAddress(msg)
        if not address.startswith("/") or address.startswith("//"):
            message = "Expected a relative address"
            raise InvalidAddress(message)
        parsed = urlsplit(address)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            msg = "Expected a relative address"
            raise InvalidAddress(msg)
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as error:
            msg = "Invalid query parameters"
            raise InvalidAddress(msg) from error
        query = dict(pairs)
        if len(query) != len(pairs):
            msg = "Duplicate query parameter"
            raise InvalidAddress(msg)
        fragment = parsed.fragment if "#" in address else None
        return cls(parsed.path, query, fragment)


class AddressRoute:
    """Compile a callable's address parameters independently of its invocation."""

    def __init__(
        self, path: str, signature: inspect.Signature, annotations: Mapping[str, Any], *, fragment: str | None = None
    ) -> None:
        if not re.fullmatch(r"/(?:[a-z][a-z0-9-]*|\{[a-z_]+\})(?:/(?:[a-z][a-z0-9-]*|\{[a-z_]+\}))*", path):
            msg = "Invalid route path"
            raise ValueError(msg)
        segments = tuple(path[1:].split("/"))
        parameters = tuple(segment[1:-1] for segment in segments if segment.startswith("{"))
        declared = signature.parameters
        if len(set(parameters)) != len(parameters) or not set(parameters) <= declared.keys():
            msg = "Every path parameter must be a callable parameter, exactly once"
            raise ValueError(msg)
        if fragment is not None:
            parameter = declared.get(fragment)
            if parameter is None or fragment in parameters or parameter.default is not None:
                msg = "Fragment must name a non-path parameter with a None default"
                raise ValueError(msg)
            annotation = annotations.get(fragment)
            while isinstance(annotation, TypeAliasType):
                annotation = annotation.__value__
            if type(None) not in get_args(annotation):
                msg = "Fragment parameter must have an optional type"
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
        self.signature = signature
        self.fragment = fragment
        self.defaults = defaults
        self.query = tuple(name for name in declared if name not in parameters and name != fragment)
        self.annotations = dict(annotations)
        self.converters = {
            name: Converter(name, r"[^/]+", partial(_decode, annotation=annotation), _encode)
            for name, annotation in annotations.items()
            if name in declared
        }
        # Broad segment patterns preserve conservative ambiguity rejection: paths
        # cannot depend on conversion failure to choose a different endpoint.
        self.pattern = RoutePattern(
            path[1:], separator="/", parameter_converters={name: self.converters[name] for name in parameters}
        )

    def decode(self, parts: AddressParts, path_arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Combine matched path values with validated query and fragment values."""
        if parts.query.keys() - set(self.query):
            msg = "Unknown query parameter"
            raise InvalidAddress(msg)
        if parts.fragment is not None and (self.fragment is None or not parts.fragment):
            msg = "Unexpected or empty fragment"
            raise InvalidAddress(msg)
        values = {**self.defaults, **path_arguments}
        if parts.fragment:
            name = self.fragment
            assert name is not None
            if "#" in parts.fragment or re.search(r"%(?![0-9a-fA-F]{2})", parts.fragment):
                msg = "Invalid fragment encoding"
                raise InvalidAddress(msg)
            try:
                fragment = unquote(parts.fragment, errors="strict")
            except UnicodeDecodeError as error:
                msg = "Invalid fragment encoding"
                raise InvalidAddress(msg) from error
            values[name] = self.converters[name].parse(fragment)
        for name, value in parts.query.items():
            values[name] = self.converters[name].parse(value)
        return values

    def arguments(self, args: tuple[object, ...], kwargs: dict[str, object]) -> dict[str, Any]:
        """Bind Python arguments, rejecting values that cannot round-trip through the address."""
        bound = self.signature.bind(*args, **kwargs)
        bound.apply_defaults()
        for name, value in bound.arguments.items():
            annotation = self.annotations[name]
            while isinstance(annotation, TypeAliasType):
                annotation = annotation.__value__
            if value is None and type(None) in get_args(annotation):
                continue
            converter = self.converters[name]
            if converter.parse(converter.build(value)) != value:
                msg = "Invalid route argument"
                raise InvalidAddress(msg)
        return dict(bound.arguments)

    def build(self, *args: object, **kwargs: object) -> str:
        """Build an address, omitting default query values and absent fragments."""
        values = self.arguments(args, kwargs)
        path = "/" + self.pattern.build(**{name: values[name] for name in self.pattern.params})
        query = urlencode(
            [
                (name, self.converters[name].build(values[name]))
                for name in self.query
                if values[name] != self.defaults[name]
            ]
        )
        address = f"{path}?{query}" if query else path
        if self.fragment is not None and values[self.fragment] is not None:
            fragment = self.converters[self.fragment].build(values[self.fragment])
            if not fragment:
                msg = "Fragment must not be empty"
                raise InvalidAddress(msg)
            address += "#" + quote(fragment, safe="")
        return address
