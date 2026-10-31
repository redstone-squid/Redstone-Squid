"""Position-native cursor coordination for materialized slicers."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import blake2s
from typing import Literal

from squid_layouts.chrome import Chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.navigation import PlannedNav, materialized_navigation_state
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
from squid_layouts.sources import POSITION_POLICY, Direction, Position, PositionPolicy


@dataclass(frozen=True, slots=True)
class MaterializedCursorRequest:
    """What a materialized slicer knows before choosing its visible segment."""

    key: str
    extent: int
    fingerprint: str
    anchors: Mapping[str, int] | None = None
    initial: Literal["start", "end"] = "start"


@dataclass(frozen=True, slots=True)
class CursorGrant:
    position: Position
    extent: int


def content_fingerprint(parts: Sequence[str]) -> str:
    """Hash the identity of all content owned by a materialized slicer."""
    return blake2s("\0".join(parts).encode(), digest_size=16).hexdigest()


@dataclass(slots=True)
class CursorCoordinator:
    """Resolve, stage, and draw every materialized cursor in one plan."""

    session: PresentationSession
    chrome: Chrome
    nav: PlannedNav | None = None
    overrides: Mapping[str, Position] | None = None
    policy: PositionPolicy = POSITION_POLICY
    _pagers: list[ScenePager] = field(default_factory=list, init=False)
    _granted: set[str] = field(default_factory=set, init=False)
    _updates: list[SessionUpdate] = field(default_factory=list, init=False)

    def grant(self, request: MaterializedCursorRequest) -> CursorGrant:
        """Resolve one keyed position through the shared precedence policy."""
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
        """Stage the cursor and scene pager produced by a materialized slice."""
        if request.key in self._granted:
            message = f"duplicate cursor key {request.key!r}"
            raise LayoutInvariantError(message)
        self._granted.add(request.key)
        extent = max(1, request.extent)
        if extent <= 1:
            return
        resolved = Position(anchor, position.offset, Direction.AROUND)
        self._pagers.append(ScenePager(request.key, resolved.offset, extent, request.fingerprint))
        self._updates.append(CursorUpdate(request.key, CursorState(resolved, extent, request.fingerprint)))

    def controls(self, key: str, position: Position, extent: int) -> list[Node]:
        """Build mandatory numeric chrome and optional navigation for a cursor."""
        if extent <= 1:
            return []
        result: list[Node] = [Footer(self.chrome.page_footer(position.offset + 1, extent), overflow=Never())]
        if self.nav is not None:
            result.extend(self.nav(materialized_navigation_state(key, position, extent, self.chrome)))
        return result

    @property
    def pagers(self) -> tuple[ScenePager, ...]:
        return tuple(self._pagers)

    @property
    def updates(self) -> tuple[SessionUpdate, ...]:
        """Return staged writes followed by garbage collection of absent cursors."""
        return (*self._updates, ActivePagers(frozenset(pager.key for pager in self._pagers)))
