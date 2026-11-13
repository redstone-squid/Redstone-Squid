"""Choose visible pages and stage their presentation state during planning.

A materialized pager already has all its content, so its cursor offset is a page
index. Each pager asks the coordinator to resolve its remembered position, then
records the page it actually displays. The resulting updates accompany the scene;
the frontend applies them after delivery succeeds. A controlled pager takes its
page from the application instead, so the coordinator neither reads nor stores a
cursor for it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import blake2s

from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.ownership import Controlled
from squid_ui.pages import PagePosition, PagePositionPolicy, RevealPage, resolve_page_position
from squid_ui.planning.navigation import PagerNav
from squid_ui.presentation import (
    ActivePagers,
    CursorState,
    CursorUpdate,
    PresentationState,
    SessionUpdate,
)
from squid_ui.primitives.constraints import FIRST_PAGE, Never, PageOwnership, RoutedPage, resolve_page
from squid_ui.primitives.nodes import Footer, Node


@dataclass(frozen=True, slots=True)
class MaterializedCursorRequest:
    """What a materialized slicer knows before choosing its visible segment."""

    key: str
    extent: int
    fingerprint: str
    anchors: Mapping[str, int] | None = None
    owner: PageOwnership = FIRST_PAGE


@dataclass(frozen=True, slots=True)
class CursorGrant:
    position: PagePosition
    extent: int


def content_fingerprint(parts: Sequence[str]) -> str:
    """Hash the identity of all content owned by a materialized slicer."""
    digest = blake2s(digest_size=16)
    for part in parts:
        encoded = part.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


@dataclass(slots=True)
class CursorCoordinator:
    """Coordinate page positions, navigation, and staged updates for one candidate.

    Give each candidate its own coordinator so a rejected layout cannot change
    remembered positions or contribute pagers to the selected scene.
    """

    session: PresentationState
    chrome: Chrome
    nav: PagerNav | None = None
    overrides: Mapping[str, PagePosition] | None = None
    policy: PagePositionPolicy = resolve_page_position
    _pagers: list[scene.Pager] = field(default_factory=list, init=False)
    _granted: set[str] = field(default_factory=set, init=False)
    _updates: list[SessionUpdate] = field(default_factory=list, init=False)

    def grant(self, request: MaterializedCursorRequest) -> CursorGrant:
        """Resolve one keyed position through the shared precedence policy.

        A controlled page skips that policy: the application's value wins, clamped to the extent.
        """
        extent = max(1, request.extent)
        owner = request.owner
        if isinstance(owner, Controlled | RoutedPage):
            return CursorGrant(PagePosition(page=resolve_page(owner.value, extent)), extent)
        cursor = self.session.cursor(request.key)
        anchor = cursor.position.anchor
        anchored_page = None if request.anchors is None or anchor is None else request.anchors.get(anchor)
        override = None if self.overrides is None else self.overrides.get(request.key)
        if isinstance(owner, RevealPage) and owner.request != cursor.reveal_request and request.anchors is not None:
            revealed = request.anchors.get(owner.anchor)
            if revealed is not None and override is None:
                override = PagePosition(owner.anchor, revealed)
        position = self.policy(
            override=override,
            anchored=PagePosition(anchor, anchored_page) if anchored_page is not None else None,
            stale=bool(cursor.fingerprint and cursor.fingerprint != request.fingerprint),
            stored=cursor.position if request.key in self.session.cursors else None,
            initial=PagePosition(page=resolve_page(owner.initial, extent)),
            upper_bound=extent - 1,
        )
        return CursorGrant(position, extent)

    def record(
        self,
        request: MaterializedCursorRequest,
        position: PagePosition,
        *,
        anchor: str | None = None,
    ) -> None:
        """Stage the cursor and scene pager produced by a materialized slice."""
        if request.key in self._granted:
            message = f"duplicate cursor key {request.key!r}"
            raise LayoutInvariantError(message)
        self._granted.add(request.key)
        extent = max(1, request.extent)
        if extent <= 1 and not isinstance(request.owner, RevealPage):
            return
        resolved = PagePosition(anchor, position.page)
        if extent > 1:
            self._pagers.append(scene.Pager(request.key, resolved.page, extent, request.fingerprint))
        if not isinstance(request.owner, Controlled | RoutedPage):
            self._updates.append(
                CursorUpdate(
                    request.key,
                    CursorState(
                        resolved,
                        extent,
                        request.fingerprint,
                        request.owner.request if isinstance(request.owner, RevealPage) else None,
                    ),
                )
            )

    def controls(self, key: str, position: PagePosition, extent: int, owner: PageOwnership = FIRST_PAGE) -> list[Node]:
        """Build mandatory numeric chrome and optional navigation for a cursor."""
        if extent <= 1:
            return []
        result: list[Node] = [Footer(self.chrome.page_footer(position.page + 1, extent), overflow=Never())]
        if self.nav is not None:
            result.extend(self.nav(key, position.page, extent, self.chrome, owner))
        return result

    @property
    def pagers(self) -> tuple[scene.Pager, ...]:
        return tuple(self._pagers)

    @property
    def updates(self) -> tuple[SessionUpdate, ...]:
        """Return staged writes followed by garbage collection of absent cursors.

        Controlled pagers have no stored cursor, so a cursor left from when one was
        uncontrolled is collected too.
        """
        kept = frozenset(update.key for update in self._updates if isinstance(update, CursorUpdate))
        return (*self._updates, ActivePagers(kept))
