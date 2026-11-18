"""Planning output and live bindings retained by the delivering host.

Only the scene is serializable. Handlers, resources, diagnostics, and presentation
updates remain here; hosts apply updates only after successfully delivering the scene.
"""

from collections.abc import Callable, Mapping
from dataclasses import field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from squid_ui._dataclass import immutable
from squid_ui.authoring.document import Document
from squid_ui.authoring.presentations import FidelityEffect
from squid_ui.interactions import ActionBinding
from squid_ui.interactions.forms import FormBinding
from squid_ui.planning.allocation import AllocationSelection
from squid_ui.planning.allocation_policy import SearchDiagnostics
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.presentation_state import SessionUpdate
from squid_ui.scene.model import Body, Scene

if TYPE_CHECKING:
    from squid_ui.layout import LayoutContext


class CertificateScope(StrEnum):
    VIEW = "view"
    FAMILY = "family"


class PlanSeverity(StrEnum):
    ADAPTATION = "adaptation"
    DEGRADATION = "degradation"
    WARNING = "warning"


class PlanReuse(StrEnum):
    """How much prior planner work produced this result."""

    MISS = "miss"
    EXACT = "exact"
    ALLOCATION = "allocation"


@immutable
class PlanEvent:
    code: str
    path: str
    message: str
    severity: PlanSeverity = PlanSeverity.ADAPTATION


@immutable
class RegionReport:
    """The selected allocation and reconciled position of a materialized region."""

    key: str
    allocation: ResourceCost
    pages: int
    page: int
    anchor: str | None


@immutable
class PlanReport:
    events: tuple[PlanEvent, ...] = ()
    scene_fingerprint: str = ""
    regions: tuple[RegionReport, ...] = ()
    search: SearchDiagnostics | None = None


@immutable
class PlanMetrics:
    """Planner instrumentation kept outside deterministic reports and scene payloads."""

    cache_hit: bool = False
    reuse: PlanReuse = PlanReuse.MISS
    work_used: int = 0
    """Shared search operations in this call, including cache fingerprinting; zero on retained turns."""
    generated_states: int = 0
    target_evaluations: int = 0
    certified_combinations: int = 0
    context_rounds: int = 0
    partition_hint: bool = False
    """A cached partition was offered to search; it still required current certification."""


@immutable
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
    effects: tuple[FidelityEffect, ...] = field(default=(), kw_only=True)
    """Scoped fidelity losses from the selected composition, without a second ranking model."""
    session_updates: tuple[SessionUpdate, ...] = ()
    """Presentation changes to apply only after successful delivery.

    Planning reads session state without committing these changes. The frontend must
    discard them if delivery fails so planning does not move stored page or selection
    values ahead of the display the reader received.
    """

    layout: LayoutContext | None = field(default=None, kw_only=True, compare=False, repr=False)
    certificate: CertificateScope = field(default=CertificateScope.VIEW, kw_only=True)
    visible_components: frozenset[str] | None = field(default=None, kw_only=True)
    query_document: Document[Any, Any] | None = field(default=None, kw_only=True, repr=False, compare=False)
    _choices: tuple[tuple[OccurrenceId, str], ...] = field(default=(), kw_only=True, repr=False, compare=False)

    allocation: AllocationSelection[PlanResult[BodyT]] | None = field(default=None, kw_only=True)

    page_selector: Callable[[Mapping[OccurrenceId, int]], PlanResult[BodyT]] | None = field(
        default=None, kw_only=True, repr=False, compare=False
    )

    @property
    def lossless(self) -> bool:
        """Whether the selected output preserves all authored content."""
        return not self.effects

    def for_page(self, coordinates: Mapping[OccurrenceId, int]) -> PlanResult[BodyT]:
        """Select already certified output while retaining allocation diagnostics."""
        if self.page_selector is not None:
            return self.page_selector(coordinates)
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
