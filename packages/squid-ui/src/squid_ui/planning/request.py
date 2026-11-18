"""One plan's inputs and exact replay identity."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypedDict, override

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.pages import PagePosition
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.identity import FingerprintCache
from squid_ui.planning.pagination.navigation import PlannedNav
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.target import Target
from squid_ui.presentation_state import PresentationState

if TYPE_CHECKING:
    from squid_ui.planning.composition_request import CompositionOptions, CompositionPlanner


DEFAULT_SEARCH_BUDGET = 512


class Identity:
    """Identity comparison that also keeps its value alive while a planning snapshot does."""

    __slots__ = ("value",)

    def __init__(self, value: object) -> None:
        self.value = value

    @override
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Identity) and self.value is other.value


class StaticPlanOptions(TypedDict, total=False):
    """The planner inputs a sessionless render accepts.

    Split from :class:`PlanOptions` so that an entry point which plans no session cannot be
    handed one: `render_static` takes this, and rejecting `session` there is the point of it.
    """

    chrome: Chrome
    localization: Localization
    palette: Palette
    strict: bool
    nav: PlannedNav | None
    reservation: ResourceCost
    inspect_layout: bool
    composition: CompositionOptions | None


class PlanOptions(StaticPlanOptions, total=False):
    """Every planner input except the target, which each entry point defaults differently.

    Spelled as a TypedDict so that a function forwarding planner inputs declares them once
    rather than restating ten parameters and ten forwardings. :class:`PlanRequest` remains the
    value the planner sees; this is only how a caller's keywords reach it.
    """

    positions: Mapping[str, PagePosition] | None
    session: PresentationState | None
    search_budget: int


@dataclass(frozen=True, slots=True)
class PlanRequest[BodyT: scene.Body, RenderTargetT, AdapterT]:
    """Carry the target, display preferences, and presentation state through one plan.

    These are the caller's inputs. A backend can reduce the target's limits for a
    reservation or resolve framework labels into the requested language without
    replacing values in this request.

    exact_key identifies the original objects for replaying one owner's live result.
    Caches are supplied separately because their contents outlive this call.
    """

    target: Target[Any, BodyT, RenderTargetT, AdapterT]
    chrome: Chrome = DEFAULT_CHROME
    localization: Localization = NEUTRAL
    palette: Palette = DEFAULT_PALETTE
    strict: bool = False
    reservation: ResourceCost = EMPTY_COST
    positions: Mapping[str, PagePosition] | None = None
    nav: PlannedNav | None = None
    session: PresentationState | None = None
    composition: CompositionOptions | CompositionPlanner[RenderTargetT, BodyT] | None = None
    search_budget: int = DEFAULT_SEARCH_BUDGET
    inspect_layout: bool = False
    partial_layout: bool = False
    fingerprints: FingerprintCache | None = field(default=None, compare=False, repr=False)
    presentation: PresentationState = field(init=False, compare=False, repr=False)
    """`session`, or the empty state standing in for it -- resolved once, so that a request
    without a session still has one stable presentation identity for exact replay."""

    def __post_init__(self) -> None:
        if self.search_budget < 1:
            message = "planner search budget must be positive"
            raise ValueError(message)
        object.__setattr__(self, "presentation", self.session if self.session is not None else PresentationState())

    def allocation_key(self) -> tuple[object, ...]:
        """Capture the environment shared by exact replay and allocation reuse."""
        return (
            Identity(self.target),
            Identity(self.chrome),
            Identity(self.localization),
            Identity(self.palette),
            self.reservation,
            self.strict,
            Identity(self.nav),
            getattr(self.nav, "version", 0),
            self.composition is None,
            self.search_budget,
            self.inspect_layout,
            self.partial_layout,
        )

    def exact_key(self) -> tuple[object, ...]:
        """Include binding-bearing configuration and a copy of transient positions.

        Source identity and presentation revision are checked by PlanningSnapshot.
        Navigation factories can expose a changing integer version.
        """
        return (
            *self.allocation_key(),
            Identity(self.composition),
            None if self.positions is None else tuple(sorted(self.positions.items())),
        )


__all__ = ["Identity", "PlanOptions", "PlanRequest", "StaticPlanOptions"]
