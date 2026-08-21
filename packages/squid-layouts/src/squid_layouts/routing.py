"""Stateless control identity: routes that own their own custom id format.

The third interactivity tier. A `Mount` keeps handlers in memory and issues an id per
render generation; a route does the opposite — the id *is* the state pointer, the store
owns the state, and the control keeps working after a restart, a redeploy, or a year.

A route is one format string. Both directions are derived from it, so building an id and
matching one cannot drift apart:

    EDIT_BUILD = Route("edit:build:{build_id:int}")
    EDIT_BUILD.id(build_id=5)           # "edit:build:5"
    EDIT_BUILD.pattern.fullmatch(...)   # {"build_id": 5}

A parameter's format spec names its converter, the way Werkzeug spells `<int:build_id>`:
the converter supplies the pattern that parameter matches, so a tighter type is a tighter
route rather than a check the handler has to remember.

Values may not contain ``:`` because that is the field separator; a route that needs
richer arguments should point at a stored row instead of encoding one, which is the whole
premise of the tier. That rule is not enforced on its own — it falls out of the one
invariant `id` checks, below.

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
    """Regex source matching this parameter's field. Must never match `_SEPARATOR`."""
    parse: Callable[[str], Any]
    build: Callable[[Any], str]
    sample: Any
    """A value for `Router`'s registration-time overlap probe."""


CONVERTERS: dict[str, Converter] = {
    # \x01 cannot occur in a route literal, so a probe id built from it only collides with
    # another route when that route genuinely accepts anything in the field.
    "str": Converter("str", f"[^{_SEPARATOR}]+", str, str, "\x01"),
    "int": Converter("int", r"\d+", int, str, 0),
}
"""The converters a format spec may name. Deliberately small; add one when a route needs it."""


@dataclass(frozen=True, slots=True)
class Route:
    """A named family of custom ids, and the parameters one carries."""

    format: str
    params: tuple[str, ...] = field(init=False)
    converters: tuple[Converter, ...] = field(init=False)
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
        names: list[str] = []
        converters: list[Converter] = []
        captured: list[str] = []
        wildcards: list[str] = []
        template: list[str] = []
        for literal, name, spec, conversion in Formatter().parse(self.format):
            captured.append(re.escape(literal))
            wildcards.append(re.escape(literal))
            template.append(literal.replace("{", "{{").replace("}", "}}"))
            if name is None:
                continue
            if not name.isidentifier():
                message = f"route {self.format!r}: {name!r} is not a usable parameter name"
                raise ValueError(message)
            if name in names:
                message = f"route {self.format!r}: parameter {name!r} appears more than once"
                raise ValueError(message)
            if conversion:
                message = f"route {self.format!r}: parameter {name!r} may not carry a conversion"
                raise ValueError(message)
            converter = CONVERTERS.get(spec or "str")
            if converter is None:
                message = f"route {self.format!r}: parameter {name!r} names unknown converter {spec!r}"
                raise ValueError(message)
            names.append(name)
            converters.append(converter)
            captured.append(f"(?P<{name}>{converter.pattern})")
            wildcards.append(f"(?:{converter.pattern})")
            template.append(f"{{{name}}}")
        if not self.format:
            message = "a route needs a non-empty format"
            raise ValueError(message)
        object.__setattr__(self, "params", tuple(names))
        object.__setattr__(self, "converters", tuple(converters))
        object.__setattr__(self, "pattern", re.compile("".join(captured)))
        object.__setattr__(self, "anonymous", "".join(wildcards))
        object.__setattr__(self, "template", "".join(template))

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
