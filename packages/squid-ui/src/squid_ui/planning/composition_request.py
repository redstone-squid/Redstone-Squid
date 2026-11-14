"""Opt into fixed composition choices while joint search is being implemented."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from squid_ui import scene
from squid_ui.planning.composition import OccurrenceId

if TYPE_CHECKING:
    from squid_ui.document import DocumentLike
    from squid_ui.planning.request import PlanRequest
    from squid_ui.planning.result import PlanResult


@dataclass(frozen=True, slots=True)
class CompositionOptions:
    """Explicit choices keyed by occurrence path; a sole offer is selected automatically.

    This path evaluates one complete display. It neither searches for another
    representation nor paginates an overflowing selection.
    """

    selections: Mapping[OccurrenceId, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "selections", MappingProxyType(dict(self.selections)))


class CompositionPlanner[RenderTargetT, BodyT: scene.Body](Protocol):
    """A typed opt-in for custom target rules through the public planning dispatcher."""

    def plan[AdapterT](
        self, rendered: DocumentLike[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    ) -> PlanResult[BodyT]: ...
