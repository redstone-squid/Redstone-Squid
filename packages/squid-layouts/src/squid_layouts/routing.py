"""Stateless control identity: routes that own their own custom id format.

The third interactivity tier. A `Mount` keeps handlers in memory and issues an id per
render generation; a route does the opposite — the id *is* the state pointer, the store
owns the state, and the control keeps working after a restart, a redeploy, or a year.

A route is one format string, read as colon-separated segments:

    EDIT_BUILD = Route("edit:build:{build_id:int}")
    EDIT_BUILD.id(build_id=5)            # "edit:build:5"
    EDIT_BUILD.match("edit:build:5")     # {"build_id": 5}

Each segment is *exactly* a literal or one parameter — never a mix, so `build-{id}` is not
a route. That restriction is what makes overlap between two routes decidable exactly
(`Route.overlaps`) instead of by sampling, which is worth far more than the generality it
gives up: an ambiguous route table is a click that reaches the wrong handler depending on
import order.

A parameter's format spec names its converter, the way Werkzeug spells `<int:build_id>`,
and the converter supplies the pattern that field matches — so a tighter type is a tighter
route rather than a check the handler has to remember. Values cannot contain ``:``, because
that is the segment separator; a route needing richer arguments should point at a stored
row instead of encoding one, which is the whole premise of the tier.

A custom id is not a capability. Discord only echoes back an id that already exists on a
message it rendered, so ids cannot be forged and need no signing; but they are readable by
anyone who can see the card, so a handler still authorizes every click itself.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from string import Formatter
from typing import Any

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.limits import LIMITS

_SEPARATOR = ":"


@dataclass(frozen=True, slots=True)
class Converter:
    """How one parameter is spelled in an id, and what it is in Python."""

    name: str
    pattern: str
    """Regex source matching this parameter's segment. Must never match `_SEPARATOR`."""
    parse: Callable[[str], Any]
    build: Callable[[Any], str]


CONVERTERS: dict[str, Converter] = {
    "str": Converter("str", f"[^{_SEPARATOR}]+", str, str),
    "int": Converter("int", r"\d+", int, str),
}
"""The converters a format spec may name. Deliberately small; add one when a route needs it."""


@dataclass(frozen=True, slots=True)
class _Literal:
    text: str


@dataclass(frozen=True, slots=True)
class _Param:
    name: str
    converter: Converter


type _Segment = _Literal | _Param


def _split(fmt: str) -> list[str]:
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
        elif char == _SEPARATOR and depth == 0:
            pieces.append("".join(current))
            current.clear()
            index += 1
            continue
        current.append(char)
        index += 1
    pieces.append("".join(current))
    return pieces


def _segment(fmt: str, index: int, raw: str, seen: list[str]) -> _Segment:
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
            converter = CONVERTERS.get(spec or "str")
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
            # Every converter's language is non-empty and they all admit digits, so two
            # parameters always share ids. Revisit only if a disjoint converter is added.
            return True


@dataclass(frozen=True, slots=True)
class Route:
    """A named family of custom ids, and the parameters one carries."""

    format: str
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

    def __post_init__(self) -> None:
        if not self.format:
            message = "a route needs a non-empty format"
            raise ValueError(message)
        names: list[str] = []
        segments: list[_Segment] = []
        for index, raw in enumerate(_split(self.format), start=1):
            segment = _segment(self.format, index, raw, names)
            if isinstance(segment, _Param):
                names.append(segment.name)
            segments.append(segment)
        object.__setattr__(self, "segments", tuple(segments))
        object.__setattr__(self, "params", tuple(names))
        object.__setattr__(self, "converters", tuple(s.converter for s in segments if isinstance(s, _Param)))
        separator = re.escape(_SEPARATOR)
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
            _SEPARATOR.join(
                s.text.replace("{", "{{").replace("}", "}}") if isinstance(s, _Literal) else f"{{{s.name}}}"
                for s in segments
            ),
        )

    def id(self, **params: object) -> str:
        """Build one custom id, refusing anything this route could not match back.

        The check is the invariant the whole tier rests on — a route's pattern matches
        every id that route builds — rather than a list of the ways a value can be wrong.
        Empty values, values carrying the separator and values of the wrong type all fail
        here, because all three are just "the result is not one of my ids".
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
        custom_id = self.template.format(**rendered)
        if not self.pattern.fullmatch(custom_id):
            message = f"route {self.format!r} cannot match the id it built from {params!r}: {custom_id!r}"
            raise ValueError(message)
        if len(custom_id) > LIMITS.custom_id:
            message = f"route {self.format!r} built a {len(custom_id)}-character id, over the {LIMITS.custom_id} budget"
            raise LayoutInvariantError(message)
        return custom_id

    def match(self, custom_id: str) -> dict[str, Any] | None:
        """The parameters ``custom_id`` carries, or None when it belongs to another route."""
        found = self.pattern.fullmatch(custom_id)
        if found is None:
            return None
        raw = found.groupdict()
        try:
            return {
                name: converter.parse(raw[name]) for name, converter in zip(self.params, self.converters, strict=True)
            }
        except ValueError:
            # A converter's pattern admitted something its parse rejects: not our id.
            return None

    def overlaps(self, other: Route) -> bool:
        """Whether any one custom id could belong to both routes.

        Exact, not approximate, which is the point of the segment grammar: two routes share
        an id iff they have the same segment count and every position admits common text.
        Sampling one id per route misses `foo:{x}:baz` against `foo:bar:{y}`, which collide
        at `foo:bar:baz`.
        """
        if len(self.segments) != len(other.segments):
            return False
        return all(_intersect(left, right) for left, right in zip(self.segments, other.segments, strict=True))
