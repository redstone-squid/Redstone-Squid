"""Portable positions and async window-source contracts."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from hashlib import blake2s
from typing import Literal, Protocol

from squid_layouts.chrome import Chrome
from squid_layouts.text import TextLike

type PositionDirection = Literal["around", "forward", "backward"]


@dataclass(frozen=True, slots=True)
class Position:
    """A source-neutral place in ordered content.

    ``around`` asks a source to keep the anchor visible, while ``forward`` and
    ``backward`` ask for the window after or before the anchor. ``offset`` is a
    zero-based location when the source can know one and otherwise remains a hint.
    """

    anchor: str | None = None
    offset: int = 0
    direction: PositionDirection = "around"


_ORIGIN = Position()


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
        initial: Position = _ORIGIN,
        reset: Position = _ORIGIN,
        upper_bound: int | None = None,
    ) -> Position:
        """Choose and clamp a position without consulting a session or source."""
        if override is not None:
            selected = override
        elif anchored is not None:
            selected = anchored
        elif stale:
            selected = reset
        elif stored is not None:
            selected = stored
        else:
            selected = initial

        offset = max(0, selected.offset)
        if upper_bound is not None:
            offset = min(offset, max(0, upper_bound))
        return replace(selected, offset=offset)


DEFAULT_POSITION_POLICY = PositionPolicy()


@dataclass(frozen=True, slots=True)
class Window[ItemT]:
    """One fetched slice and the navigation facts a source can prove."""

    items: tuple[ItemT, ...]
    has_prev: bool
    has_next: bool
    total: int | None = None
    position: Position | None = None
    """The actual start after source-defined anchor fallback, when it differs from the request."""

    def __post_init__(self) -> None:
        if self.total is not None and self.total < 0:
            message = "Window.total cannot be negative"
            raise ValueError(message)


class WindowSource[ItemT](Protocol):
    """An async ordered source with explicit pagination capabilities."""

    countable: bool
    bidirectional: bool
    jumpable: bool

    async def fetch(self, position: Position, extent: int) -> Window[ItemT]:
        """Fetch at most ``extent`` items at or beyond ``position``."""
        ...


def window_fingerprint[ItemT](items: tuple[ItemT, ...], identity: Callable[[ItemT], str]) -> str:
    """Fingerprint only the identities in one visible window."""
    digest = blake2s(digest_size=16)
    for item in items:
        encoded = identity(item).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def window_footer[ItemT](
    chrome: Chrome,
    source: WindowSource[ItemT],
    position: Position,
    window: Window[ItemT],
    extent: int,
) -> TextLike | None:
    """Describe only the numeric position the source's capabilities can support."""
    if not window.items:
        return None
    first = position.offset + 1
    last = position.offset + len(window.items)
    if source.countable and source.jumpable and window.total is not None:
        pages = max(1, (window.total + extent - 1) // extent)
        return chrome.page_footer(position.offset // extent + 1, pages)
    if source.countable and window.total is not None:
        return chrome.approximate_total_footer(first, last, window.total)
    if source.jumpable:
        return chrome.range_footer(first, last)
    return None


class WindowCursor[ItemT]:
    """Fetch and reconcile one source window, publishing only the newest request."""

    def __init__(
        self,
        source: WindowSource[ItemT],
        extent: int,
        identity: Callable[[ItemT], str],
        *,
        initial: Position = _ORIGIN,
        policy: PositionPolicy = DEFAULT_POSITION_POLICY,
    ) -> None:
        if extent < 1:
            message = "WindowCursor.extent must be at least 1"
            raise ValueError(message)
        self.source = source
        self.extent = extent
        self.identity = identity
        self.initial = policy.resolve(initial=initial)
        self.policy = policy
        self.position = self.initial
        self.window: Window[ItemT] | None = None
        self.fingerprint = ""
        self._request_token = 0

    async def fetch(self, position: Position | None = None) -> bool:
        """Fetch a position or refresh the current anchor.

        Returns whether this request published. A newer request makes an older result a
        clean no-op even when the older source call completes last.
        """
        previous_position = self.position
        previous_fingerprint = self.fingerprint
        requested = self.policy.resolve(
            override=position,
            anchored=previous_position if position is None and previous_position.anchor is not None else None,
            stored=previous_position if self.window is not None else None,
            initial=self.initial,
        )
        self._request_token += 1
        token = self._request_token
        fetched = await self.source.fetch(requested, self.extent)
        if token != self._request_token:
            return False
        if len(fetched.items) > self.extent:
            message = f"WindowSource returned {len(fetched.items)} items for extent {self.extent}"
            raise ValueError(message)

        items = tuple(fetched.items)
        identities = tuple(self.identity(item) for item in items)
        fingerprint = window_fingerprint(items, self.identity)
        resolved = fetched.position or requested
        if identities:
            resolved = Position(identities[0], resolved.offset, "around")

        if position is None and self.window is not None:
            stale = fingerprint != previous_fingerprint
            anchored = resolved if previous_position.anchor in identities else None
            resolved = self.policy.resolve(
                anchored=anchored,
                stale=stale,
                stored=resolved,
                initial=resolved,
                reset=resolved,
            )
        else:
            resolved = self.policy.resolve(override=resolved)

        self.position = resolved
        self.window = Window(items, fetched.has_prev, fetched.has_next, fetched.total, resolved)
        self.fingerprint = fingerprint
        return True

    async def refresh(self) -> bool:
        """Re-fetch around the visible anchor."""
        return await self.fetch()

    async def next(self) -> bool:
        """Fetch the window after the current trailing item."""
        if self.window is None:
            return await self.fetch()
        if not self.window.has_next:
            return False
        anchor = self.identity(self.window.items[-1]) if self.window.items else self.position.anchor
        return await self.fetch(Position(anchor, self.position.offset + len(self.window.items), "forward"))

    async def previous(self) -> bool:
        """Fetch the window before the current leading item when the source supports it."""
        if self.window is None:
            return await self.fetch()
        if not self.source.bidirectional or not self.window.has_prev:
            return False
        anchor = self.identity(self.window.items[0]) if self.window.items else self.position.anchor
        return await self.fetch(Position(anchor, max(0, self.position.offset - self.extent), "backward"))


__all__ = [
    "DEFAULT_POSITION_POLICY",
    "Position",
    "PositionDirection",
    "PositionPolicy",
    "Window",
    "WindowCursor",
    "WindowSource",
    "window_fingerprint",
    "window_footer",
]
