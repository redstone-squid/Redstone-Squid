"""Refresh semantic action callbacks without changing a certified layout.

Only ActionControl.on_trigger is excluded from allocation comparison. All layout
and dispatch policy fields still participate, and alternate presentations remain
conservative. Rebinding follows explicit framework handler edges, including grouped
select routes, rather than traversing arbitrary application objects.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, cast, override

from squid_ui import scene
from squid_ui.authoring.semantic import ActionControl
from squid_ui.interactions import ActionBinding, ActionEvent
from squid_ui.interactions.handlers import _HandlerBinder
from squid_ui.planning.allocation import AllocationSelection, RegionPages
from squid_ui.planning.composition import OccurrenceId, SemanticOccurrence
from squid_ui.planning.offers.navigation import control_key
from squid_ui.planning.result import PlanResult
from squid_ui.planning.semantic_index import SemanticGraph


class _ActionRebinder(_HandlerBinder):
    def __init__(self, frontend: str, controls: Mapping[str, ActionControl[Any]]) -> None:
        super().__init__(frontend)
        self.controls = controls

    @override
    def action[EventT: ActionEvent[Any]](self, binding: ActionBinding[EventT]) -> ActionBinding[EventT]:
        if (control := self.controls.get(binding.key)) is not None:
            # The unchanged authored control kind guarantees this is a press binding.
            handler = cast(Callable[[EventT], Awaitable[None]], control.on_trigger)
            return self.cached(
                binding,
                lambda: replace(binding, handler=self.callback(handler), routes=self.actions(binding.routes)),
            )
        return super().action(binding)


@dataclass(frozen=True, slots=True)
class ReboundAllocation[BodyT: scene.Body]:
    """Owner-local projection of current handlers over an existing certificate.

    Wrappers are flattened on the next render so page navigation cannot accumulate
    callback generations. The underlying allocation is never mutated or shared with
    another owner by this operation.
    """

    source: AllocationSelection[PlanResult[BodyT]]
    controls: Mapping[str, ActionControl[Any]]
    frontend: str

    @property
    def identity(self) -> str:
        return self.source.identity

    @property
    def regions(self) -> Mapping[OccurrenceId, RegionPages]:
        return self.source.regions

    def visible(self, coordinates: Mapping[OccurrenceId, int]) -> Mapping[OccurrenceId, int]:
        return self.source.visible(coordinates)

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> PlanResult[BodyT]:
        result = self.source.select(coordinates)
        binder = _ActionRebinder(self.frontend, self.controls)
        return replace(result, bindings=binder.actions(result.bindings))


def rebind_allocation[BodyT: scene.Body](
    allocation: AllocationSelection[PlanResult[BodyT]],
    previous: SemanticGraph[Any, Any],
    current: SemanticGraph[Any, Any],
    frontend: str,
) -> AllocationSelection[PlanResult[BodyT]]:
    source = allocation.source if isinstance(allocation, ReboundAllocation) else allocation
    controls = dict(allocation.controls) if isinstance(allocation, ReboundAllocation) else {}
    for reference, unit in current.units.items():
        old = previous.units.get(reference)
        if (
            isinstance(unit.value, ActionControl)
            and old is not None
            and isinstance(old.value, ActionControl)
            and unit.value.on_trigger is not old.value.on_trigger
        ):
            key = control_key(SemanticOccurrence(OccurrenceId(reference.keys), current, reference))
            controls[key] = unit.value
    return ReboundAllocation(source, MappingProxyType(controls), frontend) if controls else source
