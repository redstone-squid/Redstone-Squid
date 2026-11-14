"""Remember how an interface is being viewed between renders.

PresentationState holds selected pages, open details, and other control values
managed by the UI runtime. These values affect how application data is displayed
without changing that data. Planners read them and return SessionUpdate values;
frontends apply those updates after successful delivery.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from squid_ui.planning.composition_state import RetainedComposition

from squid_ui.pages import FIRST_POSITION, PagePosition, resolve_page_position


@dataclass(frozen=True, slots=True)
class CursorState:
    """A position in keyed presentation content."""

    position: PagePosition = FIRST_POSITION
    extent: int = 1
    fingerprint: str = ""
    reveal_request: str | None = None
    source_order: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectionState:
    selected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DisclosureState:
    open: bool = False


@dataclass(frozen=True, slots=True)
class ToggleState:
    on: bool = False


@dataclass(frozen=True, slots=True)
class StrategyState:
    node_key: str
    adapter_id: str
    adapter_version: int
    strategy_id: str


@dataclass(frozen=True, slots=True)
class RegionAllocation:
    key: str
    costs: tuple[tuple[str, int], ...]
    page_sizes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RegionLayoutState:
    fingerprint: str
    allocations: tuple[RegionAllocation, ...]


@dataclass(slots=True)
class PresentationState:
    """Navigation and control values for one mounted interface, indexed by node key.

    For example, two pagers with different keys retain independent positions across
    renders. Stateful nodes use these values when declared Uncontrolled; Controlled
    nodes instead take their values from application state on every render.

    Use the mutators or apply_updates so a change also advances revision. Planner
    caches use that revision to notice changes; directly editing the dictionaries
    bypasses the check and can leave a previous plan reusable.
    """

    retained_composition: RetainedComposition | None = field(
        default=None, kw_only=True, repr=False, compare=False, metadata={"stable_identity": False}
    )
    pending_positions: dict[str, PagePosition] = field(default_factory=dict, kw_only=True)
    region_layout: RegionLayoutState | None = field(default=None, kw_only=True)
    cursors: dict[str, CursorState] = field(default_factory=dict)
    selections: dict[str, SelectionState] = field(default_factory=dict)
    disclosures: dict[str, DisclosureState] = field(default_factory=dict)
    toggles: dict[str, ToggleState] = field(default_factory=dict)
    strategies: dict[str, StrategyState] = field(default_factory=dict)
    _revision: int = field(default=0, init=False, repr=False, compare=False, metadata={"stable_identity": False})

    @property
    def revision(self) -> int:
        """Monotonic identity of changes to this live presentation session."""
        return self._revision

    def _touch(self) -> None:
        self._revision += 1

    def cursor(self, key: str) -> CursorState:
        return self.cursors.get(key, CursorState())

    def move_cursor(self, key: str, position: PagePosition) -> None:
        """Store one resolved position within the cursor's known extent."""
        current = self.cursor(key)
        selected = resolve_page_position(override=position, upper_bound=current.extent - 1)
        updated = CursorState(
            PagePosition(selected.anchor, selected.page),
            extent=current.extent,
            fingerprint=current.fingerprint,
            reveal_request=current.reveal_request,
            source_order=current.source_order,
        )
        if updated != current:
            self.cursors[key] = updated
            self._touch()

    def request_cursor(self, key: str, position: PagePosition) -> None:
        """Request a page without advancing the position of the committed display."""
        if self.pending_positions.get(key) != position:
            self.pending_positions[key] = position
            self._touch()

    def reset_cursor(self, key: str | None = None) -> None:
        if key is None:
            if not self.cursors and not self.pending_positions:
                return
            self.pending_positions.clear()
            self.cursors.clear()
            self._touch()
        else:
            pending = self.pending_positions.pop(key, None)
            if self.cursors.pop(key, None) is not None or pending is not None:
                self._touch()

    def selection(self, key: str, *, initial: tuple[str, ...] = ()) -> SelectionState:
        """The stored selection, or ``initial`` when this key was never written.

        The miss has to stay distinguishable from an explicit empty selection: a reader who
        backed out of an item chose ``()``, and re-seeding them into it would trap them.
        """
        return self.selections.get(key, SelectionState(initial))

    def select(self, key: str, selected: tuple[str, ...]) -> None:
        value = SelectionState(selected)
        if self.selections.get(key) != value:
            self.selections[key] = value
            self._touch()

    def disclosure(self, key: str, *, initial: bool = False) -> DisclosureState:
        return self.disclosures.get(key, DisclosureState(initial))

    def disclose(self, key: str, open_: bool) -> None:
        value = DisclosureState(open_)
        if self.disclosures.get(key) != value:
            self.disclosures[key] = value
            self._touch()

    def toggle(self, key: str, *, initial: bool = False) -> ToggleState:
        return self.toggles.get(key, ToggleState(initial))

    def set_toggle(self, key: str, *, on: bool) -> None:
        value = ToggleState(on)
        if self.toggles.get(key) != value:
            self.toggles[key] = value
            self._touch()

    def strategy(self, key: str, adapter_id: str, adapter_version: int) -> str | None:
        """The remembered choice, or None when it was made by a different adapter.

        A miss leaves the stale entry alone: whoever asked is about to choose again and
        overwrite it, and reading is not a good enough reason to mutate a session the
        caller may yet decide to throw away.
        """
        state = self.strategies.get(key)
        if state is None or state.adapter_id != adapter_id or state.adapter_version != adapter_version:
            return None
        return state.strategy_id

    def remember_strategy(self, key: str, adapter_id: str, adapter_version: int, strategy_id: str) -> None:
        value = StrategyState(key, adapter_id, adapter_version, strategy_id)
        if self.strategies.get(key) != value:
            self.strategies[key] = value
            self._touch()

    def apply_updates(self, updates: Sequence[SessionUpdate]) -> None:
        """Apply staged writes in order and increment revision once if any stored value changes."""
        changed = False
        for update in updates:
            match update:
                case RetainedCompositionUpdate(state=state):
                    if self.retained_composition is not state:
                        self.retained_composition = state
                        changed = True
                case RegionLayoutUpdate(state=state):
                    if self.region_layout != state:
                        self.region_layout = state
                        changed = True
                case CursorUpdate(key=key, state=cursor, requested=requested):
                    if requested is not None and self.pending_positions.get(key) == requested:
                        del self.pending_positions[key]
                        changed = True
                    if self.cursors.get(key) != cursor:
                        self.cursors[key] = cursor
                        changed = True
                case StrategyUpdate(key=key, state=strategy):
                    if self.strategies.get(key) != strategy:
                        self.strategies[key] = strategy
                        changed = True
                case ToggleUpdate(key=key, state=toggle):
                    if self.toggles.get(key) != toggle:
                        self.toggles[key] = toggle
                        changed = True
                case ActivePagers(keys=keys):
                    for stale in tuple(self.pending_positions):
                        if stale not in keys:
                            del self.pending_positions[stale]
                            changed = True
                    for stale in tuple(self.cursors):
                        if stale not in keys:
                            del self.cursors[stale]
                            changed = True
        if changed:
            self._touch()


# --- Planning's writes, staged ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CursorUpdate:
    key: str
    state: CursorState
    requested: PagePosition | None = None


@dataclass(frozen=True, slots=True)
class StrategyUpdate:
    key: str
    state: StrategyState


@dataclass(frozen=True, slots=True)
class ToggleUpdate:
    key: str
    state: ToggleState


@dataclass(frozen=True, slots=True)
class ActivePagers:
    """The keys still backed by a pager; every other cursor is forgotten."""

    keys: frozenset[str]


@dataclass(frozen=True, slots=True)
class RegionLayoutUpdate:
    state: RegionLayoutState


@dataclass(frozen=True, slots=True)
class RetainedCompositionUpdate:
    state: RetainedComposition | None


type SessionUpdate = (
    RetainedCompositionUpdate | CursorUpdate | StrategyUpdate | ToggleUpdate | ActivePagers | RegionLayoutUpdate
)
"""One presentation write that planning decided on but did not perform.

Planning only reads the session, and returns what it would have written. A frontend
applies these once its render has actually reached the user, so a failed delivery leaves
the reader exactly where the message still shows them.
"""
