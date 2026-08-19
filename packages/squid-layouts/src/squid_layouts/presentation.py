"""Closed, frontend-neutral vocabulary for presentation-only state."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CursorState:
    """A position in keyed presentation content."""

    index: int = 0
    anchor: str | None = None
    extent: int = 1
    content_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class SelectionState:
    selected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DisclosureState:
    open: bool = False


@dataclass(frozen=True, slots=True)
class StrategyState:
    node_key: str
    adapter_id: str
    adapter_version: int
    strategy_id: str


@dataclass(slots=True)
class PresentationSession:
    """Presentation state shared by a runtime and independent of domain state."""

    cursors: dict[str, CursorState] = field(default_factory=dict)
    selections: dict[str, SelectionState] = field(default_factory=dict)
    disclosures: dict[str, DisclosureState] = field(default_factory=dict)
    strategies: dict[str, StrategyState] = field(default_factory=dict)

    def cursor(self, key: str) -> CursorState:
        return self.cursors.get(key, CursorState())

    def move_cursor(self, key: str, index: int) -> None:
        current = self.cursor(key)
        self.cursors[key] = CursorState(
            max(0, min(index, current.extent - 1)),
            extent=current.extent,
            content_fingerprint=current.content_fingerprint,
        )

    def anchor_cursor(
        self,
        key: str,
        index: int,
        anchor: str | None,
        *,
        extent: int | None = None,
        content_fingerprint: str | None = None,
    ) -> None:
        current = self.cursor(key)
        self.cursors[key] = CursorState(
            max(0, index),
            anchor,
            max(1, extent if extent is not None else current.extent),
            current.content_fingerprint if content_fingerprint is None else content_fingerprint,
        )

    def reset_cursor(self, key: str | None = None) -> None:
        if key is None:
            self.cursors.clear()
        else:
            self.cursors.pop(key, None)

    def strategy(self, key: str, adapter_id: str, adapter_version: int) -> str | None:
        state = self.strategies.get(key)
        if state is None or state.adapter_id != adapter_id or state.adapter_version != adapter_version:
            self.strategies.pop(key, None)
            return None
        return state.strategy_id

    def remember_strategy(self, key: str, adapter_id: str, adapter_version: int, strategy_id: str) -> None:
        self.strategies[key] = StrategyState(key, adapter_id, adapter_version, strategy_id)
