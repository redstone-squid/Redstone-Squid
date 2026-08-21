"""One cursor lifecycle for every kind of paged content.

Three layers slice content into pages, for genuinely different reasons: the solver splits
text against the display budget, adaptation windows select options 25 at a time, and the
planner packs whole components onto root pages. Only one of those knows its page count
before it runs, so they cannot share a slicer.

What they can share is everything around the slice — where the reader was, whether the
content still matches what they were reading, what chrome to draw, and what to write back.
That is this module. A slicer asks `grant` where to cut, cuts, then `record`s what it did.

The broker never writes to the session. It reads, and collects the writes it would have
made, so the frontend can apply them only once the render has reached the reader.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import blake2s
from typing import Literal

from squid_layouts.chrome import Chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.pagination import PageNav
from squid_layouts.primitives.constraints import Never
from squid_layouts.primitives.nodes import Footer, Node
from squid_layouts.runtime.presentation import (
    ActivePagers,
    CursorState,
    CursorUpdate,
    PresentationSession,
    SessionUpdate,
)
from squid_layouts.scene.model import ScenePager
from squid_layouts.sources import DEFAULT_POSITION_POLICY, Position, PositionPolicy


@dataclass(frozen=True, slots=True)
class PageRequest:
    """What a slicer knows before it decides which page to show."""

    key: str
    pages: int
    fingerprint: str
    """Identity of the paged content, so a reader on page 4 of something else starts over."""
    anchors: Mapping[str, int] | None = None
    """Page index per anchorable item, when the slice has item identity to follow."""
    initial: Literal["start", "end"] = "start"


@dataclass(frozen=True, slots=True)
class PageGrant:
    index: int
    pages: int


def content_fingerprint(parts: Sequence[str]) -> str:
    """Hash the identity of paged content. The separator cannot occur in a Discord string."""
    return blake2s("\0".join(parts).encode(), digest_size=16).hexdigest()


@dataclass(slots=True)
class PageBroker:
    """The one place a page position is read, reconciled, written and drawn."""

    session: PresentationSession
    chrome: Chrome
    nav: PageNav | None = None
    overrides: Mapping[str, int | Position] | None = None
    """Explicit position from the caller, outranking the stored position."""
    policy: PositionPolicy = DEFAULT_POSITION_POLICY
    _pagers: list[ScenePager] = field(default_factory=list, init=False)
    _granted: set[str] = field(default_factory=set, init=False)
    _updates: list[SessionUpdate] = field(default_factory=list, init=False)

    def grant(self, request: PageRequest) -> PageGrant:
        """Resolve one keyed position. The precedence here is the whole policy.

        An anchor outranks the stale-content reset on purpose: if the item the reader was
        looking at still exists, following it beats sending them back to page 1 just
        because its neighbours changed.
        """
        pages = max(1, request.pages)
        cursor = self.session.cursor(request.key)
        override = None if self.overrides is None else self.overrides.get(request.key)
        anchored = None if request.anchors is None or cursor.anchor is None else request.anchors.get(cursor.anchor)
        position = self.policy.resolve(
            override=Position(offset=override) if isinstance(override, int) else override,
            anchored=Position(cursor.anchor, anchored) if anchored is not None else None,
            stale=bool(cursor.content_fingerprint and cursor.content_fingerprint != request.fingerprint),
            stored=Position(cursor.anchor, cursor.index) if request.key in self.session.cursors else None,
            initial=Position(offset=pages - 1 if request.initial == "end" else 0),
            upper_bound=pages - 1,
        )
        return PageGrant(position.offset, pages)

    def record(self, request: PageRequest, index: int, *, anchor: str | None = None) -> None:
        """Publish the slice a grant led to: the scene's pager record and the cursor write."""
        if request.key in self._granted:
            message = f"duplicate pager key {request.key!r}"
            raise LayoutInvariantError(message)
        self._granted.add(request.key)
        pages = max(1, request.pages)
        # A single page is not a pager: no controls, no footer, and nothing worth
        # remembering across renders.
        if pages <= 1:
            return
        self._pagers.append(ScenePager(request.key, index, pages, request.fingerprint))
        self._updates.append(CursorUpdate(request.key, CursorState(index, anchor, pages, request.fingerprint)))

    def controls(self, key: str, index: int, pages: int) -> list[Node]:
        """The footer and nav for one pager, identical wherever the slice came from.

        The footer is `Never`: dropping it under budget pressure would leave the reader
        with page buttons and no way to tell which page they are on.
        """
        if pages <= 1:
            return []
        result: list[Node] = [Footer(self.chrome.page_footer(index + 1, pages), overflow=Never())]
        if self.nav is not None:
            result.extend(self.nav(key, index, pages))
        return result

    @property
    def pagers(self) -> tuple[ScenePager, ...]:
        return tuple(self._pagers)

    @property
    def updates(self) -> tuple[SessionUpdate, ...]:
        """The cursor writes this plan earned, ending with what is still on screen.

        The trailing `ActivePagers` is the garbage collection: a cursor whose pager is
        gone — the list shrank to one page, the node left the document — is forgotten
        rather than left to strand a position nothing can reach.
        """
        return (*self._updates, ActivePagers(frozenset(pager.key for pager in self._pagers)))
