"""Opt into fixed composition or automatic joint representation and page search."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from squid_ui import scene
from squid_ui.planning.allocation_policy import RegionPolicy
from squid_ui.planning.composition import OccurrenceId

if TYPE_CHECKING:
    from squid_ui.document import DocumentLike
    from squid_ui.planning.request import PlanRequest
    from squid_ui.planning.result import PlanResult


@dataclass(frozen=True, slots=True)
class CompositionOptions:
    """Explicit choices keyed by occurrence path; a sole offer is selected automatically.

    Set automatic=True for joint search. Fixed selections and partitions then
    constrain its domain. Page coordinates only select certified output.
    Automatic work_limit covers enumeration through scene assembly; fixed mode
    retains PlanRequest.search_budget for compatibility.
    """

    selections: Mapping[OccurrenceId, str] = field(default_factory=dict)
    automatic: bool = False
    policies: Mapping[OccurrenceId, RegionPolicy] = field(default_factory=dict)
    partitions: Mapping[OccurrenceId, tuple[int, ...]] = field(default_factory=dict)
    pages: Mapping[OccurrenceId, int] = field(default_factory=dict)
    work_limit: int = 50_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "selections", MappingProxyType(dict(self.selections)))
        for name in ("policies", "partitions", "pages"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))
        if self.work_limit < 1:
            message = "composition work limit must be positive"
            raise ValueError(message)
        if not self.automatic and (self.policies or self.partitions or self.pages):
            message = "page and allocation options require automatic composition"
            raise ValueError(message)


class CompositionPlanner[RenderTargetT, BodyT: scene.Body](Protocol):
    """A typed opt-in for custom target rules through the public planning dispatcher."""

    def plan[AdapterT](
        self, rendered: DocumentLike[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    ) -> PlanResult[BodyT]: ...
