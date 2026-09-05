"""State and results shared across semantic adaptation modules."""

from collections.abc import Mapping
from dataclasses import dataclass

from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.chrome import Chrome
from squid_ui.palette import Palette
from squid_ui.planning.cursors import CursorCoordinator
from squid_ui.planning.limits import MessageLimits
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET, StrategyAxis
from squid_ui.primitives.nodes import Node
from squid_ui.runtime.presentation_state import PresentationState, SessionUpdate
from squid_ui.scene.model import PlanEvent
from squid_ui.text import Localization


@dataclass(frozen=True, slots=True)
class SemanticLowering:
    """What `lower_semantics` returns: the primitives `measure()` prices, plus lowering's side effects."""

    nodes: tuple[Node, ...]
    assets: tuple[Asset, ...] = ()
    """Files that `Download` nodes attach; the planner uploads them with the message."""
    events: tuple[PlanEvent, ...] = ()
    pagers: tuple[scene.Pager, ...] = ()
    """Every cursor lowering granted, for the scene and the mount's cursor reconciliation."""
    updates: tuple[SessionUpdate, ...] = ()
    """Session writes staged during lowering (chosen strategies, cursor positions); lowering itself writes nothing."""
    states_explored: int = 0
    """Strategy states the per-axis `choose_strategy` calls visited, summed for the plan report."""
    search_fallback: bool = False
    """Copied from `LoweringContext.search_fallback`, which no lowering path sets; always False."""


@dataclass(slots=True)
class LoweringContext:
    """Mutable state threaded through one lowering pass; `assets`, `events` and `updates` accumulate."""

    limits: MessageLimits
    chrome: Chrome
    localization: Localization
    palette: Palette
    session: PresentationState
    pages: CursorCoordinator
    capabilities: frozenset[str]
    assets: list[Asset]
    events: list[PlanEvent]
    updates: list[SessionUpdate]
    strategies: Mapping[str, str]
    """The search's strategy assignment per axis path; an axis absent here is chosen locally."""
    fallbacks: Mapping[str, int]
    """The selected rung per `FallbackContent`/`OptionalContent` path; absent means the primary."""
    search_budget: int = DEFAULT_SEARCH_BUDGET
    states_explored: int = 0
    search_fallback: bool = False
    panel_depth: int = 0
    """Positive while lowering inside a container, so a nested region does not open a second `Panel`."""


@dataclass(frozen=True, slots=True)
class FallbackAxis:
    """One semantic loss decision and the branches it can offer."""

    path: str
    branches: int
    branch_paths: tuple[str, ...]
    """One stable path per branch; decisions inside a branch are named under it."""
    priority: int = 0
    """Carried into the `DegradationEffect` and note when a branch past the primary is selected."""
    optional: bool = False
    """An `OptionalContent` axis: its second branch is empty, so selecting it is a dropped node, not a semantic step."""


@dataclass(frozen=True, slots=True)
class SemanticDecisions:
    """Every semantic choice reachable under one set of selected fallback branches."""

    strategies: tuple[StrategyAxis, ...] = ()
    fallbacks: tuple[FallbackAxis, ...] = ()
