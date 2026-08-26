"""Portable planning, adaptation, and layout measurement APIs."""

from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.discord import (
    CLASSIC_PROTOCOL_CAPABILITIES,
    V2_PROTOCOL_CAPABILITIES,
    classic_target,
    components_v2_target,
    dynamic_classic_target,
    dynamic_components_v2_target,
)
from squid_layouts.planning.layout_measurement.diagnostics import (
    LayoutOverflowError,
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
)
from squid_layouts.planning.measurement import (
    MeasuredLayout,
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
    "CLASSIC_PROTOCOL_CAPABILITIES",
    "DEFAULT_SEARCH_BUDGET",
    "POSITION_POLICY",
    "V2_PROTOCOL_CAPABILITIES",
    "AdapterProfile",
    "ClassicTarget",
    "ComponentsV2Target",
    "DiscordAdapter",
    "DiscordPy27Adapter",
    "DiscordPyAdapter",
    "DiscordTarget",
    "LayoutOverflowError",
    "MeasuredLayout",
    "PlanCache",
    "Position",
    "PositionPolicy",
    "PreparedExtension",
    "Renderable",
    "ResourceCost",
    "SolveNote",
    "SolveNoteCode",
    "SolveNoteSeverity",
    "StrategyAssignment",
    "StrategyAxis",
    "StrategyCandidate",
    "TargetProfile",
    "TargetRequirements",
    "choose_strategy",
    "classic_target",
    "components_v2_target",
    "dynamic_classic_target",
    "dynamic_components_v2_target",
    "iter_assignments",
    "measure",
    "plan",
]
