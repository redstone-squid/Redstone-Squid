"""Planning output and live bindings retained by the delivering host.

Only the scene is serializable. Handlers, resources, diagnostics, and presentation
updates remain here; hosts apply updates only after successfully delivering the scene.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum

from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding
from squid_ui.planning.allocation import AllocationSelection
from squid_ui.planning.allocation_policy import SearchDiagnostics
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.degradation import DegradationProfile
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.presentation import SessionUpdate
from squid_ui.scene.model import Body, Scene


class PlanSeverity(StrEnum):
    ADAPTATION = "adaptation"
    DEGRADATION = "degradation"
    WARNING = "warning"


class PlanReuse(StrEnum):
    """How much prior planner work produced this result."""

    MISS = "miss"
    EXACT = "exact"
    STRUCTURAL = "structural"
    INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class PlanEvent:
    code: str
    path: str
    message: str
    severity: PlanSeverity = PlanSeverity.ADAPTATION
    before: Mapping[str, int] = field(default_factory=dict)
    after: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegionReport:
    """The selected allocation and reconciled position of a materialized region."""

    key: str
    allocation: ResourceCost
    pages: int
    page: int
    anchor: str | None


@dataclass(frozen=True, slots=True)
class PlanReport:
    events: tuple[PlanEvent, ...] = ()
    logical_fingerprint: str = ""
    scene_fingerprint: str = ""
    regions: tuple[RegionReport, ...] = ()
    search: SearchDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class PlanMetrics:
    """Planner instrumentation kept outside deterministic reports and scene payloads."""

    states_explored: int = 0
    """`measure()` calls the search spent, across strategies, fallbacks, and ladder rungs."""
    cache_hit: bool = False
    reuse: PlanReuse = PlanReuse.MISS
    search_fallback: bool = False
    work_used: int = 0
    generated_states: int = 0
    target_evaluations: int = 0
    certified_combinations: int = 0


@dataclass(frozen=True, slots=True)
class PlanResult[BodyT = Body]:
    """A scene and the Python objects needed to deliver it and handle interactions.

    For example, a scene button names an action; the bindings mapping supplies its
    handler. The resources mapping supplies objects such as file sources that
    cannot be included in the scene's serialized description. Only scene crosses
    the codec.

    The report explains the planner's choices, while metrics measure the work it
    did. The frontend must apply session_updates after delivery succeeds to keep
    stored presentation state in step with what the user can see.
    """

    scene: Scene[BodyT]
    bindings: Mapping[str, ActionBinding]
    report: PlanReport
    form_bindings: Mapping[str, FormBinding] = field(default_factory=dict)
    """What each declared form key presents right now, for resolving a late submission."""
    form_plans: Mapping[str, object] = field(default_factory=dict, kw_only=True)
    """Adapter-owned presentation plans, prepared after frontend callbacks are bound."""
    resources: Mapping[str, object] = field(default_factory=dict)
    metrics: PlanMetrics = field(default_factory=PlanMetrics)
    cost: ResourceCost = field(default=EMPTY_COST, kw_only=True)
    degradation: DegradationProfile = field(default_factory=DegradationProfile, kw_only=True)
    session_updates: tuple[SessionUpdate, ...] = ()
    """Presentation changes to apply only after successful delivery.

    Planning reads session state without committing these changes. The frontend must
    discard them if delivery fails so planning does not move stored page or selection
    values ahead of the display the reader received.
    """

    allocation: AllocationSelection[PlanResult[BodyT]] | None = field(default=None, kw_only=True)

    def for_page(self, coordinates: Mapping[OccurrenceId, int]) -> PlanResult[BodyT]:
        """Select already certified output while retaining allocation diagnostics."""
        if self.allocation is None:
            message = "this result has no joint allocation"
            raise ValueError(message)
        selected = self.allocation.select(coordinates)
        return replace(
            selected,
            report=replace(selected.report, search=self.report.search),
            metrics=self.metrics,
            allocation=self.allocation,
        )
