"""Portable planning, adaptation, and layout measurement APIs."""

from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.measure import (
    LayoutOverflowError,
    MeasuredLayout,
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    measure,
)
from squid_layouts.planning.planner import plan
from squid_layouts.planning.search import (
    DEFAULT_SEARCH_BUDGET,
    StrategyAssignment,
    StrategyAxis,
    StrategyCandidate,
    choose_strategy,
    iter_assignments,
)
from squid_layouts.planning.target import PreparedExtension, ResourceCost, TargetProfile
from squid_layouts.planning.types import (
    ClassicTarget,
    ComponentsV2Target,
    DiscordAdapter,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    DiscordTarget,
    Renderable,
    TargetRequirements,
)
from squid_layouts.sources import POSITION_POLICY, Position, PositionPolicy

__all__ = [
    "AdapterProfile",
    "ClassicTarget",
    "ComponentsV2Target",
    "DEFAULT_SEARCH_BUDGET",
    "DiscordAdapter",
    "DiscordPy27Adapter",
    "DiscordPyAdapter",
    "DiscordTarget",
    "POSITION_POLICY",
    "LayoutOverflowError",
    "MeasuredLayout",
    "PlanCache",
    "Position",
    "PositionPolicy",
    "PreparedExtension",
    "ResourceCost",
    "Renderable",
    "SolveNote",
    "SolveNoteCode",
    "SolveNoteSeverity",
    "StrategyAssignment",
    "StrategyAxis",
    "StrategyCandidate",
    "TargetProfile",
    "TargetRequirements",
    "choose_strategy",
    "iter_assignments",
    "measure",
    "plan",
]
