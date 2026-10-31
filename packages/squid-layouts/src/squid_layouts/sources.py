"""Position tokens and asynchronous window loading."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import blake2s
from typing import Protocol

from squid_layouts.chrome import Chrome
from squid_layouts.text import TextLike


class Direction(StrEnum):
    """How a source interprets a position's anchor."""

    AROUND = "around"
    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass(frozen=True, slots=True)
class Position:
    """A source-neutral position in ordered content."""

    anchor: str | None = None
    offset: int = 0
    direction: Direction = Direction.AROUND


ORIGIN = Position()


@dataclass(frozen=True, slots=True)
class PositionPolicy:
    """Resolve the shared override/anchor/staleness/stored/initial precedence ladder."""

    def resolve(
        self,
        *,
        override: Position | None = None,
        anchored: Position | None = None,
        stale: bool = False,
        stored: Position | None = None,
        initial: Position = ORIGIN,
        fallback: Position = ORIGIN,
        upper_bound: int | None = None,
    ) -> Position:
        """Choose and clamp a position without consulting a session or source."""
        if override is not None:
            selected = override
        elif anchored is not None:
            selected = anchored
        elif stale:
            selected = fallback
        elif stored is not None:
            selected = stored
        else:
            selected = initial

        offset = max(0, selected.offset)
        if upper_bound is not None:
            offset = min(offset, max(0, upper_bound))
        return replace(selected, offset=offset)


POSITION_POLICY = PositionPolicy()


class CountPrecision(StrEnum):
    """Accuracy of the total a source returns with each window."""

    NONE = "none"
    APPROXIMATE = "approximate"
    EXACT = "exact"


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    """Navigation and count facts a source can support."""

    backward: bool = False
    offsets: bool = False
    jumpable: bool = False
    count: CountPrecision = CountPrecision.NONE

    def __post_init__(self) -> None:
        if self.jumpable and not self.offsets:
            message = "a jumpable source must know offsets"
            raise ValueError(message)
        if self.count is not CountPrecision.NONE and not self.offsets:
            message = "a countable source must know offsets"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Window[ItemT]:
    """One fetched slice, including its authoritative resolved position."""

    position: Position
    items: tuple[ItemT, ...]
    has_previous: bool
    has_next: bool
    total: int | None = None

    def __post_init__(self) -> None:
        if self.position.direction is not Direction.AROUND:
            message = "a resolved Window.position must use Direction.AROUND"
            raise ValueError(message)
        if self.position.offset < 0:
            message = "a resolved Window.position cannot have a negative offset"
            raise ValueError(message)
        if self.total is not None and self.total < 0:
            message = "Window.total cannot be negative"
            raise ValueError(message)


class WindowSource[ItemT](Protocol):
    """An async ordered source with one validated capability declaration."""

    capabilities: SourceCapabilities

    async def fetch(self, position: Position, extent: int) -> Window[ItemT]:
        """Fetch at most ``extent`` items around or beyond ``position``."""
        ...


@dataclass(frozen=True, slots=True)
class LoadedWindow[ItemT]:
    """A fetched window and the fingerprint of only its visible identities."""

    window: Window[ItemT]
    fingerprint: str

    @property
    def position(self) -> Position:
        return self.window.position


def window_fingerprint[ItemT](items: tuple[ItemT, ...], identity: Callable[[ItemT], str]) -> str:
    """Fingerprint only the identities in one visible window."""
    digest = blake2s(digest_size=16)
    for item in items:
        encoded = identity(item).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


class WindowLoader[ItemT]:
    """Load immutable windows while dropping completions older than the newest request."""

    def __init__(
        self,
        source: WindowSource[ItemT],
        extent: int,
        identity: Callable[[ItemT], str],
        *,
        initial: Position = ORIGIN,
        policy: PositionPolicy = POSITION_POLICY,
    ) -> None:
        if extent < 1:
            message = "WindowLoader.extent must be at least 1"
            raise ValueError(message)
        self.source = source
        self.extent = extent
        self.identity = identity
        self.initial = policy.resolve(initial=initial)
        self.policy = policy
        self._request_token = 0

    async def load(
        self,
        position: Position | None = None,
        *,
        previous: LoadedWindow[ItemT] | None = None,
    ) -> LoadedWindow[ItemT] | None:
        """Load a requested position or refresh around the previously visible anchor."""
        previous_position = None if previous is None else previous.position
        requested = self.policy.resolve(
            override=position,
            anchored=previous_position if position is None and previous_position is not None else None,
            stored=previous_position,
            initial=self.initial,
        )
        self._request_token += 1
        token = self._request_token
        fetched = await self.source.fetch(requested, self.extent)
        if token != self._request_token:
            return None
        self._validate(fetched)

        fingerprint = window_fingerprint(fetched.items, self.identity)
        if position is None and previous is not None:
            identities = tuple(self.identity(item) for item in fetched.items)
            resolved = self.policy.resolve(
                anchored=fetched.position if previous.position.anchor in identities else None,
                stale=fingerprint != previous.fingerprint,
                stored=fetched.position,
                initial=fetched.position,
                fallback=fetched.position,
            )
            fetched = replace(fetched, position=resolved)
        return LoadedWindow(fetched, fingerprint)

    async def next(self, current: LoadedWindow[ItemT]) -> LoadedWindow[ItemT] | None:
        """Load the window after the visible trailing identity."""
        window = current.window
        if not window.has_next:
            return None
        anchor = self.identity(window.items[-1]) if window.items else window.position.anchor
        requested = Position(anchor, window.position.offset + len(window.items), Direction.FORWARD)
        return await self.load(requested, previous=current)

    async def previous(self, current: LoadedWindow[ItemT]) -> LoadedWindow[ItemT] | None:
        """Load the window before the visible leading identity."""
        window = current.window
        if not self.source.capabilities.backward or not window.has_previous:
            return None
        anchor = self.identity(window.items[0]) if window.items else window.position.anchor
        requested = Position(anchor, max(0, window.position.offset - self.extent), Direction.BACKWARD)
        return await self.load(requested, previous=current)

    def _validate(self, window: Window[ItemT]) -> None:
        if len(window.items) > self.extent:
            message = f"WindowSource returned {len(window.items)} items for extent {self.extent}"
            raise ValueError(message)
        capabilities = self.source.capabilities
        if not capabilities.backward and window.has_previous:
            message = "a forward-only source returned has_previous=True"
            raise ValueError(message)
        if capabilities.count is CountPrecision.NONE and window.total is not None:
            message = "an uncountable source returned a total"
            raise ValueError(message)
        if capabilities.count is not CountPrecision.NONE and window.total is None:
            message = "a countable source omitted its total"
            raise ValueError(message)
        if (
            capabilities.count is CountPrecision.EXACT
            and window.total is not None
            and window.position.offset + len(window.items) > window.total
        ):
            message = "an exact source returned a window beyond its total"
            raise ValueError(message)


def window_footer[ItemT](
    chrome: Chrome, source: WindowSource[ItemT], loaded: LoadedWindow[ItemT], extent: int
) -> TextLike | None:
    """Describe only the numeric position the source's capabilities support."""
    window = loaded.window
    capabilities = source.capabilities
    if not window.items or not capabilities.offsets:
        return None
    first = window.position.offset + 1
    last = window.position.offset + len(window.items)
    if capabilities.count is CountPrecision.EXACT and capabilities.jumpable and window.total is not None:
        pages = max(1, (window.total + extent - 1) // extent)
        return chrome.page_footer(window.position.offset // extent + 1, pages)
    if capabilities.count is CountPrecision.EXACT and window.total is not None:
        return chrome.total_range_footer(first, last, window.total)
    if capabilities.count is CountPrecision.APPROXIMATE and window.total is not None:
        return chrome.approximate_total_footer(first, last, window.total)
    return chrome.range_footer(first, last)


__all__ = [
    "ORIGIN",
    "POSITION_POLICY",
    "CountPrecision",
    "Direction",
    "LoadedWindow",
    "Position",
    "PositionPolicy",
    "SourceCapabilities",
    "Window",
    "WindowLoader",
    "WindowSource",
]
