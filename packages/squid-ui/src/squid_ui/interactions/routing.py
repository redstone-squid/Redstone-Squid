"""Colon-delimited control IDs backed by the shared routing engine.

Routes identify actions across restarts; handlers still authorize each interaction.
Aliases accept old IDs while id() always emits the canonical format.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from squid_ui.errors import LayoutInvariantError
from squid_ui.routing.pattern import Converter as Converter
from squid_ui.routing.pattern import RoutePattern

MAX_ROUTE_ID_LENGTH = 100
CONVERTERS: dict[str, Converter] = {
    "str": Converter("str", "[^:]+", str, str),
    "int": Converter("int", r"\d+", int, str),
}


@dataclass(frozen=True, slots=True)
class Route(RoutePattern):
    """A control route with aliases and the portable 100-character ID limit."""

    converter_types: Mapping[str, Converter] = field(
        default_factory=lambda: CONVERTERS, kw_only=True, repr=False, hash=False
    )

    def id(self, **params: object) -> str:
        """Build a canonical ID, rejecting values that do not fit the transport."""
        route_id = self.build(**params)
        if len(route_id) > MAX_ROUTE_ID_LENGTH:
            message = (
                f"route {self.format!r} built a {len(route_id)}-character id, over the {MAX_ROUTE_ID_LENGTH} budget"
            )
            raise LayoutInvariantError(message)
        return route_id


__all__ = ["CONVERTERS", "Converter", "Route"]
