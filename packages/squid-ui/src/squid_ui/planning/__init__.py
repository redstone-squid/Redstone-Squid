"""Choose a target representation for authored layout nodes and fit its limits.

plan accepts a Document or layout nodes and returns a PlanResult. Its Scene describes
the chosen display; accompanying bindings connect controls to Python handlers. Planning
does not send the display: a renderer builds the native payload and a frontend delivers it.

A Target combines a dialect, which defines a scene format and its planner, with an
adapter profile describing the renderer library's supported behavior. Limits and
reservations tell the planner how much space that particular display can use.
"""

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
from squid_ui.planning.resources import ResourceCost
from squid_ui.planning.search import (
    DEFAULT_SEARCH_BUDGET,
    StrategyAssignment,
    StrategyAxis,
    StrategyCandidate,
    choose_strategy,
    iter_assignments,
)
from squid_ui.planning.target import PreparedExtension, Target
from squid_ui.sources import POSITION_RESOLVER, Position, PositionResolver
from squid_ui.target_types import (
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
