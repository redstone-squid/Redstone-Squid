"""Transport-independent segmented route patterns and parameter converters."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from string import Formatter
from typing import Any


@dataclass(frozen=True, slots=True)
class Converter:
    """How one parameter is spelled in an id, and what it is in Python."""

    name: str
    pattern: str
    """Regex source matching this parameter's segment. Must not match the route separator."""
    parse: Callable[[str], Any]
    build: Callable[[Any], str]


@dataclass(frozen=True, slots=True)
class _Literal:
    text: str


@dataclass(frozen=True, slots=True)
class _Param:
    name: str
    converter: Converter


type _Segment = _Literal | _Param


def _split(fmt: str, separator: str) -> list[str]:
    """Split on the separator, ignoring one inside a replacement field.

    `{build_id:int}` carries a colon of its own, so a plain `str.split` would cut a
    parameter in half.
    """
    pieces: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(fmt):
        char = fmt[index]
        pair = fmt[index : index + 2]
        if depth == 0 and pair in ("{{", "}}"):
            current.append(pair)
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == separator and depth == 0:
            pieces.append("".join(current))
            current.clear()
            index += 1
            continue
        current.append(char)
        index += 1
    pieces.append("".join(current))
    return pieces


def _segment(
    fmt: str,
    index: int,
    raw: str,
    seen: list[str],
    converters: Mapping[str, Converter],
    parameters: Mapping[str, Converter],
) -> _Segment:
    """Read one colon-delimited segment as either a literal or a single parameter."""
    if not raw:
        message = f"route {fmt!r}: segment {index} is empty"
        raise ValueError(message)
    parsed = list(Formatter().parse(raw))
    match parsed:
        case [(literal, None, _, _)]:
            return _Literal(literal)
        case [("", name, spec, conversion)] if name is not None:
            if not name.isidentifier():
                message = f"route {fmt!r}: {name!r} is not a usable parameter name"
                raise ValueError(message)
            if name in seen:
                message = f"route {fmt!r}: parameter {name!r} appears more than once"
                raise ValueError(message)
            if conversion:
                message = f"route {fmt!r}: parameter {name!r} may not carry a conversion"
                raise ValueError(message)
            converter = parameters.get(name) if name in parameters and not spec else converters.get(spec or "str")
            if converter is None:
                message = f"route {fmt!r}: parameter {name!r} names unknown converter {spec!r}"
                raise ValueError(message)
            return _Param(name, converter)
        case _:
            message = (
                f"route {fmt!r}: segment {index} ({raw!r}) must be a literal or one {{parameter}}, not a mix. "
                "Exact segments are what make route overlap decidable."
            )
            raise ValueError(message)


def _intersect(left: _Segment, right: _Segment) -> bool:
    """Whether two segments admit any common text."""
    match left, right:
        case _Literal(), _Literal():
            return left.text == right.text
        case _Literal(), _Param():
            return re.fullmatch(right.converter.pattern, left.text) is not None
        case _Param(), _Literal():
            return re.fullmatch(left.converter.pattern, right.text) is not None
        case _:
            # Arbitrary converter regexes cannot be compared exactly. Treat unknown
            # intersections as overlaps rather than making dispatch order significant.
            return True


@dataclass(frozen=True, slots=True)
class RoutePattern:
    """Compile whole-segment parameters for matching and reverse generation.

    Adapters choose a separator and converters. Unknown parameter intersections are
    rejected conservatively: a table never depends on registration order to resolve
    an ambiguous address. Query strings, fragments, and transport limits live above
    this layer.
    """

    format: str
    aliases: tuple[str, ...] = ()
    separator: str = field(default=":", kw_only=True)
    converter_types: Mapping[str, Converter] = field(default_factory=dict, kw_only=True, repr=False, hash=False)
    parameter_converters: Mapping[str, Converter] = field(default_factory=dict, kw_only=True, repr=False, hash=False)
    segments: tuple[_Segment, ...] = field(init=False, repr=False)
    params: tuple[str, ...] = field(init=False)
    converters: tuple[Converter, ...] = field(init=False, repr=False)
    """Parallel to `params`."""
    pattern: re.Pattern[str] = field(init=False)
    anonymous: str = field(init=False, repr=False)
    """This route's pattern source with its groups made non-capturing.

    A router alternates these into one template: named groups from different routes would
    collide, and the parameters are read back by re-matching the individual route anyway.
    """
    template: str = field(init=False, repr=False)
    """`format` with the converter specs stripped, because `"{x:int}".format(x=5)` raises."""
    _alias_routes: tuple[RoutePattern, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.format:
            message = "a route needs a non-empty format"
            raise ValueError(message)
        if len(self.separator) != 1 or self.separator in "{}":
            message = "Route separator must be one character other than a brace"
            raise ValueError(message)
        converters = {
            "str": Converter("str", f"[^{re.escape(self.separator)}]+", str, str),
            **self.converter_types,
        }
        names: list[str] = []
        segments: list[_Segment] = []
        for index, raw in enumerate(_split(self.format, self.separator), start=1):
            segment = _segment(self.format, index, raw, names, converters, self.parameter_converters)
            if isinstance(segment, _Param):
                names.append(segment.name)
            segments.append(segment)
        object.__setattr__(self, "segments", tuple(segments))
        object.__setattr__(self, "params", tuple(names))
        object.__setattr__(self, "converters", tuple(s.converter for s in segments if isinstance(s, _Param)))
        separator = re.escape(self.separator)
        object.__setattr__(
            self,
            "pattern",
            re.compile(
                separator.join(
                    re.escape(s.text) if isinstance(s, _Literal) else f"(?P<{s.name}>{s.converter.pattern})"
                    for s in segments
                )
            ),
        )
        object.__setattr__(
            self,
            "anonymous",
            separator.join(
                re.escape(s.text) if isinstance(s, _Literal) else f"(?:{s.converter.pattern})" for s in segments
            ),
        )
        object.__setattr__(
            self,
            "template",
            self.separator.join(
                s.text.replace("{", "{{").replace("}", "}}") if isinstance(s, _Literal) else f"{{{s.name}}}"
                for s in segments
            ),
        )
        alias_routes = tuple(
            RoutePattern(
                alias,
                separator=self.separator,
                converter_types=self.converter_types,
                parameter_converters=self.parameter_converters,
            )
            for alias in self.aliases
        )
        canonical_signature = {
            name: converter.name for name, converter in zip(self.params, self.converters, strict=True)
        }
        variants = (self, *alias_routes)
        for alias in alias_routes:
            alias_signature = {
                name: converter.name for name, converter in zip(alias.params, alias.converters, strict=True)
            }
            if alias_signature != canonical_signature:
                message = (
                    f"route {self.format!r}: alias {alias.format!r} must carry the same parameters and converters; "
                    f"expected {canonical_signature}, got {alias_signature}"
                )
                raise ValueError(message)
        for index, left in enumerate(variants):
            for right in variants[index + 1 :]:
                if left._overlaps_format(right):
                    message = (
                        f"route {self.format!r}: formats {left.format!r} and {right.format!r} overlap; "
                        "one id could decode through both"
                    )
                    raise ValueError(message)
        object.__setattr__(self, "_alias_routes", alias_routes)
        if alias_routes:
            object.__setattr__(
                self,
                "anonymous",
                "|".join(f"(?:{route.anonymous})" for route in variants),
            )

    def build(self, **params: object) -> str:
        """Build a canonical ID from exactly the declared parameter names.

        Converter output must match the route's pattern, which rejects empty segments,
        separators, and unsupported representations. Adapters may additionally validate
        Python input types and impose length limits.
        """
        missing = set(self.params) - set(params)
        unknown = set(params) - set(self.params)
        if missing or unknown:
            detail = ", ".join(
                part
                for part in (
                    f"missing {sorted(missing)}" if missing else "",
                    f"unknown {sorted(unknown)}" if unknown else "",
                )
                if part
            )
            message = f"route {self.format!r} takes {list(self.params)}: {detail}"
            raise ValueError(message)
        rendered = {
            name: converter.build(params[name]) for name, converter in zip(self.params, self.converters, strict=True)
        }
        route_id = self.template.format(**rendered)
        if not self.pattern.fullmatch(route_id):
            message = f"route {self.format!r} cannot match the id it built from {params!r}: {route_id!r}"
            raise ValueError(message)
        return route_id

    def match(self, route_id: str) -> dict[str, Any] | None:
        """The parameters ``route_id`` carries, or None when it belongs to another route."""
        found = self.pattern.fullmatch(route_id)
        if found is not None:
            raw = found.groupdict()
            try:
                return {
                    name: converter.parse(raw[name])
                    for name, converter in zip(self.params, self.converters, strict=True)
                }
            except ValueError:
                # A converter's pattern admitted something its parse rejects: not our id.
                return None
        for alias in self._alias_routes:
            if (params := alias.match(route_id)) is not None:
                return params
        return None

    def _overlaps_format(self, other: RoutePattern) -> bool:
        """Whether the canonical formats of two routes admit a common id."""
        if self.separator != other.separator:
            message = "Cannot compare routes with different separators"
            raise ValueError(message)
        if len(self.segments) != len(other.segments):
            return False
        return all(_intersect(left, right) for left, right in zip(self.segments, other.segments, strict=True))

    def canonical_starts_with(self, segment: str) -> bool:
        """Whether the canonical format starts with exactly the literal ``segment``."""
        first = self.segments[0]
        return isinstance(first, _Literal) and first.text == segment

    def accepts_first_segment(self, segment: str) -> bool:
        """Whether the canonical format or an alias can start with ``segment``."""
        literal = _Literal(segment)
        return any(_intersect(route.segments[0], literal) for route in (self, *self._alias_routes))

    def overlaps(self, other: RoutePattern) -> bool:
        """Whether the canonical formats or aliases can accept the same ID.

        Literal intersections are exact. Parameter pairs conservatively overlap,
        even when their converters might accept disjoint sets of values.
        """
        return any(
            left._overlaps_format(right)
            for left in (self, *self._alias_routes)
            for right in (other, *other._alias_routes)
        )
