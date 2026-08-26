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
    nodes: tuple[Node, ...]
    assets: tuple[Asset, ...] = ()
    events: tuple[PlanEvent, ...] = ()
    pagers: tuple[scene.Pager, ...] = ()
    updates: tuple[SessionUpdate, ...] = ()
    states_explored: int = 0
    search_fallback: bool = False


@dataclass(slots=True)
class LoweringContext:
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
    fallbacks: Mapping[str, int]
    search_budget: int = DEFAULT_SEARCH_BUDGET
    states_explored: int = 0
    search_fallback: bool = False
    panel_depth: int = 0


@dataclass(frozen=True, slots=True)
class FallbackAxis:
    """One semantic loss decision and the branches it can offer."""

    path: str
    branches: int
    branch_paths: tuple[str, ...]
    """One stable path per branch; decisions inside a branch are named under it."""
    priority: int = 0
    optional: bool = False


@dataclass(frozen=True, slots=True)
class SemanticDecisions:
    """Every semantic choice reachable under one set of selected fallback branches."""

    strategies: tuple[StrategyAxis, ...] = ()
    fallbacks: tuple[FallbackAxis, ...] = ()
