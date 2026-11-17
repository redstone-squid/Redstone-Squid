"""Separate a live allocation's action templates from its current callbacks.

Ordinary semantic actions become slots when a live allocation is prepared. Each
owner projects those templates with its current handlers. Alternate presentations
retain their chosen bindings because their source correspondence can change keys.
"""

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from squid_ui import scene
from squid_ui.authoring.presentations import Alternatives, FidelityEffect
from squid_ui.authoring.semantic import ActionControl, ActionControls, FallbackContent
from squid_ui.errors import LayoutInvariantError
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.interactions.forms import FormBinding
from squid_ui.interactions.handlers import _HandlerBinder
from squid_ui.native import NativeTraits
from squid_ui.overflow import FIRST_PAGE
from squid_ui.planning.allocation import AllocationSelection, AllocationSnapshot, RegionPages
from squid_ui.planning.composition import OccurrenceId, SemanticOccurrence
from squid_ui.planning.identity_cache import CacheStats, IdentityCache
from squid_ui.planning.interaction_handlers import RouteSelection
from squid_ui.planning.offers.navigation import control_key, grouped_control_key
from squid_ui.planning.resources import ResourceCost
from squid_ui.planning.result import PlanMetrics, PlanReport, PlanResult
from squid_ui.planning.semantic_index import SemanticGraph


@dataclass(frozen=True, slots=True)
class ActionSlot:
    """A reference to an ordinary action in the owner's current control snapshot."""

    key: str


@dataclass(frozen=True, slots=True)
class ActionGroupSlot:
    key: str
    mode: ActionMode
    routes: Mapping[str, ActionSlot]


@dataclass(frozen=True, slots=True)
class PageBindings:
    """Live values for one certified page, kept outside its reusable display data."""

    actions: Mapping[str, ActionBinding]
    forms: Mapping[str, FormBinding]
    form_plans: Mapping[str, object]
    resources: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ActionPage[BodyT: scene.Body]:
    """Certified display data and action slots, separate from projected bindings.

    Unsupported binding kinds remain live and owner-local. Ordinary slots contain
    only keys; their handlers and dispatch policy come from the current controls.
    """

    scene: scene.Scene[BodyT]
    report: PlanReport
    metrics: PlanMetrics
    cost: ResourceCost
    effects: tuple[FidelityEffect, ...]
    bindings: Mapping[str, ActionSlot | ActionGroupSlot]
    slot: int

    @classmethod
    def prepare(
        cls,
        result: PlanResult[BodyT],
        controls: Mapping[str, ActionControl[Any]],
        groups: Mapping[str, Mapping[str, str]] | None = None,
        *,
        slot: int = 0,
    ) -> tuple[ActionPage[BodyT], PageBindings]:
        bindings: dict[str, ActionSlot | ActionGroupSlot] = {}
        live: dict[str, ActionBinding] = {}
        for key, binding in result.bindings.items():
            if isinstance(binding.handler, RouteSelection):
                aliases = (groups or {}).get(key)
                if aliases is None or not all(aliases.get(route) in controls for route in binding.routes):
                    live[key] = binding
                    continue
                # Framework grouped controls carry per-action policy in their routes.
                if binding.guard is not None or binding.busy is not None or binding.record is not None or binding.label:
                    message = "grouped action template has unexpected dispatch policy"
                    raise LayoutInvariantError(message)
                bindings[key] = ActionGroupSlot(
                    key, binding.mode, MappingProxyType({route: ActionSlot(aliases[route]) for route in binding.routes})
                )
            elif key in controls:
                bindings[key] = ActionSlot(key)
            else:
                live[key] = binding
        return (
            cls(
                result.scene,
                result.report,
                result.metrics,
                result.cost,
                result.effects,
                MappingProxyType(bindings),
                slot,
            ),
            PageBindings(MappingProxyType(live), result.form_bindings, result.form_plans, result.resources),
        )

    @staticmethod
    def _bind_action(
        slot: ActionSlot, controls: Mapping[str, ActionControl[Any]], *, key: str | None = None
    ) -> ActionBinding:
        control = controls[slot.key]
        return ActionBinding(
            slot.key if key is None else key,
            control.on_trigger,
            control.mode,
            guard=control.guard,
            busy=control.busy,
            label=control.label,
            record=control.record,
        )

    def bind(self, controls: Mapping[str, ActionControl[Any]], frontend: str, live: PageBindings) -> PlanResult[BodyT]:
        bindings = dict(live.actions)
        for key, template in self.bindings.items():
            match template:
                case ActionSlot():
                    bindings[key] = self._bind_action(template, controls)
                case ActionGroupSlot():
                    routes = MappingProxyType(
                        {key: self._bind_action(slot, controls, key=key) for key, slot in template.routes.items()}
                    )
                    bindings[key] = ActionBinding(template.key, RouteSelection(routes), template.mode, routes=routes)
        prepared = _HandlerBinder(frontend).actions(bindings)
        return PlanResult(
            self.scene,
            MappingProxyType(dict(prepared)),
            self.report,
            form_bindings=live.forms,
            form_plans=live.form_plans,
            resources=live.resources,
            metrics=self.metrics,
            cost=self.cost,
            effects=self.effects,
        )


@dataclass(frozen=True, slots=True)
class ActionAllocation[BodyT: scene.Body]:
    """One owner's current callbacks and bounded projections of certified templates.

    A new callback generation replaces this view while retaining its source templates.
    Page turns retain the view and its cache. Neither operation changes delivery state.
    """

    source: AllocationSelection[ActionPage[BodyT]]
    controls: Mapping[str, ActionControl[Any]]
    frontend: str
    live: Mapping[int, PageBindings]
    regions: Mapping[OccurrenceId, RegionPages]
    capacity: int = 32
    max_bytes: int = 1024 * 1024
    _cache: IdentityCache[PlanResult[BodyT]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "controls", MappingProxyType(dict(self.controls)))
        object.__setattr__(self, "live", MappingProxyType(dict(self.live)))
        object.__setattr__(self, "regions", MappingProxyType(dict(self.regions)))
        object.__setattr__(
            self, "_cache", IdentityCache[PlanResult[BodyT]](capacity=self.capacity, max_bytes=self.max_bytes)
        )

    def cache_snapshot(self) -> CacheStats:
        """Inspect page-projection reuse without exposing cached callbacks."""
        return self._cache.snapshot()

    @property
    def identity(self) -> str:
        return self.source.identity

    def visible(self, coordinates: Mapping[OccurrenceId, int]) -> Mapping[OccurrenceId, int]:
        return self.source.visible(coordinates)

    @staticmethod
    def _projection_weight(result: PlanResult) -> int:
        # Borrowed application objects behind handlers/providers belong to the owner.
        def binding_weight(binding: ActionBinding) -> int:
            return (
                sys.getsizeof(binding)
                + 128
                + sum(len(key.encode()) + binding_weight(route) for key, route in binding.routes.items())
            )

        return (
            len(scene.Codec.dumps(result.scene).encode())
            + sys.getsizeof(result)
            + 256
            + sum(len(key.encode()) + binding_weight(binding) for key, binding in result.bindings.items())
        )

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> PlanResult[BodyT]:
        result = self.source.select(coordinates)
        if (cached := self._cache.get(result)) is not None:
            return cached
        projected = result.bind(self.controls, self.frontend, self.live[result.slot])
        # Failed frontend binding must not publish a partially prepared projection.
        self._cache.put(result, projected, weight=self._projection_weight(projected))
        return projected


def prepare_action_allocation[BodyT: scene.Body](
    allocation: AllocationSelection[PlanResult[BodyT]],
    graph: SemanticGraph[Any, Any],
    frontend: str,
) -> AllocationSelection[PlanResult[BodyT]]:
    """Prepare slots once, then replace only the current callback snapshot on reuse."""
    controls: dict[str, ActionControl[Any]] = {}
    groups: dict[str, dict[str, str]] = {}
    for reference, unit in graph.units.items():
        if not isinstance(unit.value, ActionControl):
            continue
        parent = unit.parent
        while parent is not None:
            ancestor = graph.units[parent]
            if (
                isinstance(ancestor.value, Alternatives | FallbackContent)
                or NativeTraits.of(ancestor.value).fallback is not None
            ):
                break
            parent = ancestor.parent
        else:
            key = control_key(SemanticOccurrence(OccurrenceId(reference.keys), graph, reference))
            controls[key] = unit.value
            if unit.parent is not None and isinstance(graph.units[unit.parent].value, ActionControls):
                group = SemanticOccurrence(OccurrenceId(unit.parent.keys), graph, unit.parent)
                groups.setdefault(grouped_control_key(group), {})[unit.value.key] = key
    if isinstance(allocation, ActionAllocation):
        if allocation.controls == controls and allocation.frontend == frontend:
            return allocation
        return ActionAllocation(allocation.source, controls, frontend, allocation.live, allocation.regions)
    if not isinstance(allocation, AllocationSnapshot):
        return allocation
    live: dict[int, PageBindings] = {}

    def prepare(result: PlanResult[BodyT]) -> ActionPage[BodyT]:
        slot = len(live)
        page, bindings = ActionPage.prepare(result, controls, groups, slot=slot)
        live[slot] = bindings
        return page

    templates = AllocationSnapshot[ActionPage[BodyT]].project(
        allocation,
        prepare,
        regions={key: replace(region, owner=FIRST_PAGE) for key, region in allocation.regions.items()},
    )
    return ActionAllocation(templates, controls, frontend, live, allocation.regions)
