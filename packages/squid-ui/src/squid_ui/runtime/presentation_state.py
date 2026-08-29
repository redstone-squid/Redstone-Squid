"""Closed, frontend-neutral vocabulary for presentation-only state."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from squid_ui.sources import ORIGIN, POSITION_RESOLVER, Direction, Position


@dataclass(frozen=True, slots=True)
class CursorState:
    """A position in keyed presentation content."""

    position: Position = ORIGIN
    extent: int = 1
    fingerprint: str = ""


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


@dataclass(slots=True)
class PresentationState:
    """Presentation state shared by a runtime and independent of domain state."""

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

    def move_cursor(self, key: str, position: Position) -> None:
        """Store one resolved position within the cursor's known extent."""
        current = self.cursor(key)
        selected = POSITION_RESOLVER.resolve(override=position, upper_bound=current.extent - 1)
        updated = CursorState(
            Position(selected.anchor, selected.offset, Direction.AROUND),
            extent=current.extent,
            fingerprint=current.fingerprint,
        )
        if updated != current:
            self.cursors[key] = updated
            self._touch()

    def reset_cursor(self, key: str | None = None) -> None:
        if key is None:
            if not self.cursors:
                return
            self.cursors.clear()
            self._touch()
        else:
            if self.cursors.pop(key, None) is not None:
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


# --- Planning's writes, staged ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CursorUpdate:
    key: str
    state: CursorState


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


type SessionUpdate = CursorUpdate | StrategyUpdate | ToggleUpdate | ActivePagers
"""One presentation write that planning decided on but did not perform.

Planning only reads the session, and returns what it would have written. A frontend
applies these once its render has actually reached the user, so a failed delivery leaves
the reader exactly where the message still shows them.
"""


def apply_updates(session: PresentationState, updates: Sequence[SessionUpdate]) -> None:
    """Commit planning's presentation writes, in the order planning made them."""
    changed = False
    for update in updates:
        match update:
            case CursorUpdate(key=key, state=cursor):
                if session.cursors.get(key) != cursor:
                    session.cursors[key] = cursor
                    changed = True
            case StrategyUpdate(key=key, state=strategy):
                if session.strategies.get(key) != strategy:
                    session.strategies[key] = strategy
                    changed = True
            case ToggleUpdate(key=key, state=toggle):
                if session.toggles.get(key) != toggle:
                    session.toggles[key] = toggle
                    changed = True
            case ActivePagers(keys=keys):
                for stale in tuple(session.cursors):
                    if stale not in keys:
                        del session.cursors[stale]
                        changed = True
    if changed:
        session._touch()
