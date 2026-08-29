"""Portable planning, adaptation, and layout measurement APIs."""

from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.dialect import TargetDialect
from squid_ui.planning.discord import (
    CLASSIC_PROTOCOL_CAPABILITIES,
    V2_PROTOCOL_CAPABILITIES,
    classic_target,
    components_v2_target,
    dynamic_classic_target,
    dynamic_components_v2_target,
)
from squid_ui.planning.layout_measurement.diagnostics import (
    LayoutOverflowError,
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
)
from squid_ui.planning.layout_measurement.solver import (
    MeasuredLayout,
    measure,
)
from squid_ui.planning.planner import plan
from squid_ui.planning.search import (
    DEFAULT_SEARCH_BUDGET,
    StrategyAssignment,
    StrategyAxis,
    StrategyCandidate,
    choose_strategy,
    iter_assignments,
)
from squid_ui.planning.target import PreparedExtension, ResourceCost, Target
from squid_ui.planning.types import (
    ClassicTarget,
    ComponentsV2Target,
    DiscordAdapter,
    DiscordPy27Adapter,
    DiscordPyAdapter,
    DiscordTarget,
    HtmlAdapter,
    HtmlTarget,
    Renderable,
    RenderTarget,
    SlackAdapter,
    SlackHomeTarget,
    SlackMessageTarget,
    SlackModalTarget,
    SlackSdk343Adapter,
    SlackSdkAdapter,
    SlackTarget,
)
from squid_ui.sources import POSITION_RESOLVER, Position, PositionResolver

__all__ = [
    "CLASSIC_PROTOCOL_CAPABILITIES",
    "DEFAULT_SEARCH_BUDGET",
    "POSITION_RESOLVER",
    "V2_PROTOCOL_CAPABILITIES",
    "AdapterProfile",
    "ClassicTarget",
    "ComponentsV2Target",
    "DiscordAdapter",
    "DiscordPy27Adapter",
    "DiscordPyAdapter",
    "DiscordTarget",
    "HtmlAdapter",
    "HtmlTarget",
    "LayoutOverflowError",
    "MeasuredLayout",
    "PlanCache",
    "PlanMemo",
    "Position",
    "PositionResolver",
    "PreparedExtension",
    "RenderTarget",
    "Renderable",
    "ResourceCost",
    "SlackAdapter",
    "SlackHomeTarget",
    "SlackMessageTarget",
    "SlackModalTarget",
    "SlackSdk343Adapter",
    "SlackSdkAdapter",
    "SlackTarget",
    "SolveNote",
    "SolveNoteCode",
    "SolveNoteSeverity",
    "StrategyAssignment",
    "StrategyAxis",
    "StrategyCandidate",
    "Target",
    "TargetDialect",
    "choose_strategy",
    "classic_target",
    "components_v2_target",
    "dynamic_classic_target",
    "dynamic_components_v2_target",
    "iter_assignments",
    "measure",
    "plan",
]
