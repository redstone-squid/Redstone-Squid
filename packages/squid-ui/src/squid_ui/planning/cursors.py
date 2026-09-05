"""Position-native cursor coordination for materialized slicers."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import blake2s
from typing import Literal

from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.navigation import PlannedNav, materialized_navigation_state
from squid_ui.primitives.constraints import Never
from squid_ui.primitives.nodes import Footer, Node
from squid_ui.runtime.presentation_state import (
    ActivePagers,
    CursorState,
    CursorUpdate,
    PresentationState,
    SessionUpdate,
)
from squid_ui.sources import POSITION_RESOLVER, Direction, Position, PositionResolver


@dataclass(frozen=True, slots=True)
class MaterializedCursorRequest:
    """What a materialized slicer knows before choosing its visible segment."""

    key: str
    extent: int
    """Page count; treated as at least one."""
    fingerprint: str
    """Identity of the sliced content; a stored cursor with a different one is stale and resets to the origin."""
    anchors: Mapping[str, int] | None = None
    """Page offset per anchor name, for a stored position that names an anchor rather than an offset."""
    initial: Literal["start", "end"] = "start"
    """Which end to open on when nothing else decides the position."""


@dataclass(frozen=True, slots=True)
class CursorGrant:
    """A position clamped to `[0, extent - 1]`, with `extent` at least one."""

    position: Position
    extent: int


def content_fingerprint(parts: Sequence[str]) -> str:
    """Hash the identity of all content owned by a materialized slicer."""
    return blake2s("\0".join(parts).encode(), digest_size=16).hexdigest()


@dataclass(slots=True)
class CursorCoordinator:
    """Resolve, stage, and draw every materialized cursor in one plan.

    Each key is granted once and recorded once; `updates` is what the plan commits to the session.
    """

    session: PresentationState
    chrome: Chrome
    nav: PlannedNav | None = None
    overrides: Mapping[str, Position] | None = None
    """Caller-supplied positions by key (`PlanRequest.positions`); these win over everything stored."""
    policy: PositionResolver = POSITION_RESOLVER
    _pagers: list[scene.Pager] = field(default_factory=list, init=False)
    _granted: set[str] = field(default_factory=set, init=False)
    _updates: list[SessionUpdate] = field(default_factory=list, init=False)

    def grant(self, request: MaterializedCursorRequest) -> CursorGrant:
        """Resolve one keyed position: override, then anchor, then the stored cursor unless stale, then `initial`.

        Pure: nothing is staged until `record`.
        """
        extent = max(1, request.extent)
        cursor = self.session.cursor(request.key)
        anchor = cursor.position.anchor
        anchored_offset = None if request.anchors is None or anchor is None else request.anchors.get(anchor)
        position = self.policy.resolve(
            override=None if self.overrides is None else self.overrides.get(request.key),
            anchored=Position(anchor, anchored_offset) if anchored_offset is not None else None,
            stale=bool(cursor.fingerprint and cursor.fingerprint != request.fingerprint),
            stored=cursor.position if request.key in self.session.cursors else None,
            initial=Position(offset=extent - 1 if request.initial == "end" else 0),
            upper_bound=extent - 1,
        )
        return CursorGrant(position, extent)

    def record(
        self,
        request: MaterializedCursorRequest,
        position: Position,
        *,
        anchor: str | None = None,
    ) -> None:
        """Stage the cursor write and scene pager for one slice; a single-page slice stages nothing.

        Raises `LayoutInvariantError` when `request.key` has already been recorded in this plan.
        """
        if request.key in self._granted:
            message = f"duplicate cursor key {request.key!r}"
            raise LayoutInvariantError(message)
        self._granted.add(request.key)
        extent = max(1, request.extent)
        if extent <= 1:
            return
        resolved = Position(anchor, position.offset, Direction.AROUND)
        self._pagers.append(scene.Pager(request.key, resolved.offset, extent, request.fingerprint))
        self._updates.append(CursorUpdate(request.key, CursorState(resolved, extent, request.fingerprint)))

    def controls(self, key: str, position: Position, extent: int) -> list[Node]:
        """The page footer, then `nav`'s controls when one is set; empty for a single page."""
        if extent <= 1:
            return []
        result: list[Node] = [Footer(self.chrome.page_footer(position.offset + 1, extent), overflow=Never())]
        if self.nav is not None:
            result.extend(self.nav(materialized_navigation_state(key, position, extent, self.chrome)))
        return result

    @property
    def pagers(self) -> tuple[scene.Pager, ...]:
        """Every multi-page cursor recorded so far, in record order."""
        return tuple(self._pagers)

    @property
    def updates(self) -> tuple[SessionUpdate, ...]:
        """The staged cursor writes, then an `ActivePagers` that drops every cursor not recorded here."""
        return (*self._updates, ActivePagers(frozenset(pager.key for pager in self._pagers)))
