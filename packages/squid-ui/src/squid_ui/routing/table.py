"""Associate route patterns with endpoints without choosing how to invoke them."""

from typing import Any

from squid_ui.routing.pattern import RoutePattern


class RouteTable[Endpoint]:
    """Resolve unambiguous patterns to adapter-owned endpoints.

    Endpoints can be stable handles whose handlers are rebound after registration.
    Adapters own freezing and replacement rules; the table only owns route identity.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[RoutePattern, Endpoint]] = []

    def add(self, pattern: RoutePattern, endpoint: Endpoint) -> None:
        for existing, _ in self._entries:
            if pattern.overlaps(existing):
                message = f"route {pattern.format!r} overlaps the already-registered {existing.format!r}"
                raise ValueError(message)
        self._entries.append((pattern, endpoint))

    def match(self, value: str) -> tuple[Endpoint, dict[str, Any]] | None:
        for pattern, endpoint in self._entries:
            arguments = pattern.match(value)
            if arguments is not None:
                return endpoint, arguments
        return None
