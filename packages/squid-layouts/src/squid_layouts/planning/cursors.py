"""One cursor lifecycle for every kind of paged content.

Three layers slice content into pages, for genuinely different reasons: the solver splits
text against the display budget, adaptation windows select options 25 at a time, and the
planner packs whole components onto root pages. Only one of those knows its page count
before it runs, so they cannot share a slicer.

What they can share is everything around the slice — where the reader was, whether the
content still matches what they were reading, what chrome to draw, and what to write back.
That is this module. A slicer asks `grant` where to cut, cuts, then `record`s what it did.
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
from squid_layouts.runtime.presentation import PresentationSession
from squid_layouts.scene.model import ScenePager


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
    overrides: Mapping[str, int] | None = None
    """Explicit `page=` from the caller: "show page N", outranking the stored position."""
    _pagers: list[ScenePager] = field(default_factory=list, init=False)
    _granted: set[str] = field(default_factory=set, init=False)

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
        if override is not None:
            index = override
        elif anchored is not None:
            index = anchored
        elif cursor.content_fingerprint and cursor.content_fingerprint != request.fingerprint:
            index = 0
        elif request.key in self.session.cursors:
            index = cursor.index
        else:
            index = pages - 1 if request.initial == "end" else 0
        return PageGrant(max(0, min(index, pages - 1)), pages)

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
        self.session.anchor_cursor(
            request.key,
            index,
            anchor,
            extent=pages,
            content_fingerprint=request.fingerprint,
        )

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
