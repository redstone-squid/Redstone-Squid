"""Stateless control identity: routes that own their own custom id format.

The third interactivity tier. A `Mount` keeps handlers in memory and issues an id per
render generation; a route does the opposite — the id *is* the state pointer, the store
owns the state, and the control keeps working after a restart, a redeploy, or a year.

A route is one format string. Both directions are derived from it, so building an id and
matching one cannot drift apart:

    EDIT_BUILD = Route("edit:build:{build_id}")
    EDIT_BUILD.id(build_id=5)           # "edit:build:5"
    EDIT_BUILD.pattern.fullmatch(...)   # {"build_id": "5"}

Values may not contain ``:`` because that is the field separator; a route that needs
richer arguments should point at a stored row instead of encoding one, which is the whole
premise of the tier.
"""

import re
from dataclasses import dataclass, field
from string import Formatter

from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.limits import LIMITS

_SEPARATOR = ":"


@dataclass(frozen=True, slots=True)
class Route:
    """A named family of custom ids, and the parameters one carries."""

    format: str
    params: tuple[str, ...] = field(init=False)
    pattern: re.Pattern[str] = field(init=False)
    anonymous: str = field(init=False, repr=False)
    """This route's pattern source with its groups made non-capturing.

    A router alternates these into one template: named groups from different routes would
    collide, and the parameters are read back by re-matching the individual route anyway.
    """

    def __post_init__(self) -> None:
        names: list[str] = []
        captured: list[str] = []
        wildcards: list[str] = []
        for literal, name, spec, conversion in Formatter().parse(self.format):
            captured.append(re.escape(literal))
            wildcards.append(re.escape(literal))
            if name is None:
                continue
            if not name.isidentifier():
                message = f"route {self.format!r}: {name!r} is not a usable parameter name"
                raise ValueError(message)
            if name in names:
                message = f"route {self.format!r}: parameter {name!r} appears more than once"
                raise ValueError(message)
            if spec or conversion:
                message = f"route {self.format!r}: parameter {name!r} may not carry a format spec"
                raise ValueError(message)
            names.append(name)
            captured.append(f"(?P<{name}>[^{_SEPARATOR}]+)")
            wildcards.append(f"(?:[^{_SEPARATOR}]+)")
        if not self.format:
            message = "a route needs a non-empty format"
            raise ValueError(message)
        object.__setattr__(self, "params", tuple(names))
        object.__setattr__(self, "pattern", re.compile("".join(captured)))
        object.__setattr__(self, "anonymous", "".join(wildcards))

    def id(self, **params: object) -> str:
        """Build one custom id, refusing anything Discord would reject at send time."""
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
            raise LayoutInvariantError(message)
        values: dict[str, str] = {}
        for name, value in params.items():
            rendered = str(value)
            if not rendered:
                message = f"route {self.format!r}: parameter {name!r} may not be empty"
                raise LayoutInvariantError(message)
            if _SEPARATOR in rendered:
                message = f"route {self.format!r}: parameter {name!r} may not contain {_SEPARATOR!r}"
                raise LayoutInvariantError(message)
            values[name] = rendered
        custom_id = self.format.format(**values)
        if len(custom_id) > LIMITS.custom_id:
            message = f"route {self.format!r} built a {len(custom_id)}-character id, over the {LIMITS.custom_id} budget"
            raise LayoutInvariantError(message)
        return custom_id

    def match(self, custom_id: str) -> dict[str, str] | None:
        """The parameters ``custom_id`` carries, or None when it belongs to another route."""
        found = self.pattern.fullmatch(custom_id)
        return None if found is None else found.groupdict()
