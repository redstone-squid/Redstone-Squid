"""Choose a target representation for authored layout nodes and fit its limits.

plan accepts a Document or layout nodes and returns a PlanResult. Its Scene describes
the chosen display; accompanying bindings connect controls to Python handlers. Planning
does not send the display: a renderer builds the native payload and a frontend delivers it.

A Target combines a dialect, which defines a scene format and its planner, with an
adapter profile describing the renderer library's supported behavior. Limits and
reservations tell the planner how much space that particular display can use.

Shared planning composes authored nodes into candidate representations, allocates
region pages, and searches the legal choices and page cuts. Certification checks
every reachable page combination against target and allocation constraints before
search can retain the resulting layout. Allocation scoring compares certified
families using preferences, fidelity, soft goals, and page turns.
"""

from squid_ui.pages import PagePosition, PagePositionPolicy, resolve_page_position
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.allocation_policy import AllocationGoal, RegionPolicy, SearchCompletion, SearchDiagnostics
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.composition import AtomicContract, OccurrenceId
from squid_ui.planning.composition_planner import AutomaticComposition, CompositionRejectedError, FixedComposition
from squid_ui.planning.dialect import TargetDialect
from squid_ui.planning.joint_search import JointExhaustedError, JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.planner import plan
from squid_ui.planning.request import DEFAULT_SEARCH_BUDGET, CompositionOptions
from squid_ui.planning.resources import ResourceCost
from squid_ui.planning.result import (
    CertificateScope,
    PlanEvent,
    PlanMetrics,
    PlanReport,
    PlanResult,
    PlanReuse,
    PlanSeverity,
)
from squid_ui.planning.snapshot import PlanningSnapshot
from squid_ui.planning.spread import MessageSpread, plan_spread
from squid_ui.planning.target import PreparedExtension, Target
from squid_ui.target_types import Renderable, RenderTarget

__all__ = [
    "DEFAULT_SEARCH_BUDGET",
    "AdapterProfile",
    "AllocationGoal",
    "AtomicContract",
    "AutomaticComposition",
    "CertificateScope",
    "CompositionOptions",
    "CompositionRejectedError",
    "FixedComposition",
    "JointExhaustedError",
    "JointInfeasibleError",
    "JointUnsupportedError",
    "MessageSpread",
    "OccurrenceId",
    "PagePosition",
    "PagePositionPolicy",
    "PlanCache",
    "PlanEvent",
    "PlanMetrics",
    "PlanReport",
    "PlanResult",
    "PlanReuse",
    "PlanSeverity",
    "PlanningSnapshot",
    "PreparedExtension",
    "RegionPolicy",
    "RenderTarget",
    "Renderable",
    "ResourceCost",
    "SearchCompletion",
    "SearchDiagnostics",
    "Target",
    "TargetDialect",
    "plan",
    "plan_spread",
    "resolve_page_position",
]
