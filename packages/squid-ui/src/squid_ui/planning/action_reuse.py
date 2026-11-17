"""Refresh semantic action callbacks without changing a certified layout.

Only ActionControl.on_trigger is excluded from allocation comparison. All layout
and dispatch policy fields still participate, and alternate presentations remain
conservative. Rebinding follows explicit framework handler edges, including grouped
select routes, rather than traversing arbitrary application objects.
"""

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, cast, override

from squid_ui import scene
from squid_ui.authoring.semantic import ActionControl
from squid_ui.errors import LayoutInvariantError
from squid_ui.interactions import ActionBinding, ActionEvent, PressEvent
from squid_ui.interactions.handlers import _HandlerBinder
from squid_ui.planning.allocation import AllocationSelection, AllocationSnapshot, CertifiedAllocation, RegionPages
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
class ProjectionCacheSnapshot:
    entries: int
    hits: int
    misses: int
    evictions: int


class _ProjectionCache[BodyT: scene.Body]:
    """Bound rebinding results by certified output identity within one generation."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            message = "projection cache capacity must be positive"
            raise ValueError(message)
        self.capacity = capacity
        self.entries: OrderedDict[int, tuple[PlanResult[BodyT], PlanResult[BodyT]]] = OrderedDict()
        self.hits = self.misses = self.evictions = 0

    def get(self, source: PlanResult[BodyT]) -> PlanResult[BodyT] | None:
        entry = self.entries.get(id(source))
        if entry is None:
            self.misses += 1
            return None
        # Entries retain the source, so its identity cannot be recycled while cached.
        self.entries.move_to_end(id(source))
        self.hits += 1
        return entry[1]

    def put(self, source: PlanResult[BodyT], result: PlanResult[BodyT]) -> None:
        self.entries[id(source)] = (source, result)
        self.entries.move_to_end(id(source))
        while len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
            self.evictions += 1

    def snapshot(self) -> ProjectionCacheSnapshot:
        return ProjectionCacheSnapshot(len(self.entries), self.hits, self.misses, self.evictions)


@dataclass(frozen=True, slots=True)
class ReboundAllocation[BodyT: scene.Body]:
    """Owner-local projection of current handlers over an existing certificate.

    Wrappers are flattened on the next render so page navigation cannot accumulate
    callback generations. The underlying allocation is never mutated or shared with
    another owner by this operation. Revisited pages reuse their rebound bindings;
    the bounded cache belongs to this callback generation, never to delivery state.
    """

    source: AllocationSelection[PlanResult[BodyT]]
    controls: Mapping[str, ActionControl[Any]]
    frontend: str
    capacity: int = 32
    _cache: _ProjectionCache[BodyT] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "controls", MappingProxyType(dict(self.controls)))
        object.__setattr__(self, "_cache", _ProjectionCache[BodyT](self.capacity))

    def cache_snapshot(self) -> ProjectionCacheSnapshot:
        """Inspect page-projection reuse without exposing cached callbacks."""
        return self._cache.snapshot()

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
        if (cached := self._cache.get(result)) is not None:
            return cached
        binder = _ActionRebinder(self.frontend, self.controls)
        projected = replace(result, bindings=MappingProxyType(dict(binder.actions(result.bindings))))
        # Failed frontend binding must not publish a partially prepared projection.
        self._cache.put(result, projected)
        return projected


async def _unbound_action(event: PressEvent[Any]) -> None:
    message = "allocation action template must be bound before dispatch"
    raise LayoutInvariantError(message)


def _detach_actions[BodyT: scene.Body](
    source: AllocationSelection[PlanResult[BodyT]],
    controls: Mapping[str, ActionControl[Any]],
    frontend: str,
) -> AllocationSelection[PlanResult[BodyT]]:
    if not isinstance(source, CertifiedAllocation | AllocationSnapshot):
        # Custom selectors have no enumerable certified outputs. Keep their existing
        # ownership contract rather than introspecting application-owned state.
        return source
    templates = {key: replace(control, on_trigger=_unbound_action) for key, control in controls.items()}

    def detach(result: PlanResult[BodyT]) -> PlanResult[BodyT]:
        binder = _ActionRebinder(frontend, templates)
        return replace(result, bindings=MappingProxyType(dict(binder.actions(result.bindings))))

    return AllocationSnapshot[PlanResult[BodyT]].project(source, detach)


def rebind_allocation[BodyT: scene.Body](
    allocation: AllocationSelection[PlanResult[BodyT]],
    previous: SemanticGraph[Any, Any],
    current: SemanticGraph[Any, Any],
    frontend: str,
) -> AllocationSelection[PlanResult[BodyT]]:
    source = allocation.source if isinstance(allocation, ReboundAllocation) else allocation
    controls = dict(allocation.controls) if isinstance(allocation, ReboundAllocation) else {}
    previous_keys = controls.keys() | set()
    changed = False
    for reference, unit in current.units.items():
        old = previous.units.get(reference)
        if (
            isinstance(unit.value, ActionControl)
            and old is not None
            and isinstance(old.value, ActionControl)
            and unit.value.on_trigger is not old.value.on_trigger
        ):
            changed = True
            key = control_key(SemanticOccurrence(OccurrenceId(reference.keys), current, reference))
            controls[key] = unit.value
    if not changed and (not isinstance(allocation, ReboundAllocation) or allocation.frontend == frontend):
        return allocation
    new_slots = {key: control for key, control in controls.items() if key not in previous_keys}
    if new_slots:
        source = _detach_actions(source, new_slots, frontend)
    return ReboundAllocation(source, controls, frontend) if controls else source
