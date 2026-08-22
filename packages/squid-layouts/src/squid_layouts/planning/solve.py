"""Temporary bounded search over primitive `Variants`, pending the unified planner frontier.

The planner still hands one lowered tree to one search here. That handshake is what the
unified search replaces: this module exists only so `measure()` can be extracted and tested
on its own first.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from itertools import count

from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.planning.degradation import DegradationProfile
from squid_layouts.planning.frontier import (
    Positions,
    VariantPath,
    VariantTopology,
    apply_semantic_topology,
    canonical_positions,
    guided_step,
    resolve_variants,
    semantic_topology,
    steppable,
    variant_notes,
    variant_profile,
    variant_state_bound,
)
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.measure import (
    LayoutOverflowError,
    MeasuredLayout,
    Pager,
    PositionState,
    SolveNote,
    _measure_once,
)
from squid_layouts.planning.navigation import PlannedNav
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET
from squid_layouts.primitives.nodes import Node
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization


@dataclass(frozen=True, slots=True)
class SolvedLayout:
    """One measured layout plus what the bounded structural search spent finding it."""

    layout: MeasuredLayout
    states_explored: int = 1
    search_fallback: bool = False
    variant_positions: tuple[tuple[VariantPath, int], ...] = ()
    variant_topology: VariantTopology = ()
    explored_variant_topologies: tuple[VariantTopology, ...] = ()

    @property
    def children(self):
        return self.layout.children

    @property
    def notes(self) -> list[SolveNote]:
        return self.layout.notes

    @property
    def failures(self) -> tuple[SolveNote, ...]:
        return self.layout.failures

    @property
    def pagers(self) -> tuple[Pager, ...]:
        return self.layout.pagers

    @property
    def components(self) -> int:
        return self.layout.components

    @property
    def overflowed(self) -> bool:
        return self.layout.overflowed

    @property
    def degradation(self) -> DegradationProfile:
        return self.layout.degradation

    @property
    def pages(self) -> int:
        return self.layout.pages

    @property
    def page(self) -> int:
        return self.layout.page

    def reposition(self, positions: Mapping[str, Position]) -> None:
        self.layout.reposition(positions)


def _measure_state(
    tree: Sequence[Node],
    positions: Positions,
    *,
    limits: V2Limits,
    chrome: Chrome,
    reserved_text: int,
    position: PositionState,
    nav: PlannedNav | None,
) -> MeasuredLayout:
    steps = variant_notes(tree, positions)
    measured = _measure_once(
        resolve_variants(tree, positions),
        limits=limits,
        chrome=chrome,
        reserved_text=reserved_text,
        position=position,
        nav=nav,
        notes=steps,
    )
    return replace(measured, degradation=measured.degradation.merged(variant_profile(tree, positions)))


def solve(
    nodes: Sequence[Node],
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    strict: bool = False,
    reserved_text: int = 0,
    position: PositionState = None,
    nav: PlannedNav | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    semantic_topology: Mapping[str, int] | None = None,
) -> SolvedLayout:
    """Search reachable rung assignments best-first and return the least degraded fit.

    `Variant.requires` is not consulted here: capability filtering belongs to the planner,
    which is the only layer that knows the target. A ladder reaching this search is a pure
    budget ladder whose rungs are all available.
    """
    if search_budget < 1:
        message = "solver search budget must be positive"
        raise ValueError(message)
    chrome = localize_chrome(chrome, localization)
    tree = list(nodes)
    selected_topology = {} if semantic_topology is None else semantic_topology
    locked = frozenset(selected_topology) if semantic_topology is not None else frozenset()

    def measure_state(positions: Positions) -> MeasuredLayout:
        return _measure_state(
            tree,
            positions,
            limits=limits,
            chrome=chrome,
            reserved_text=reserved_text,
            position=position,
            nav=nav,
        )

    initial = {} if semantic_topology is None else apply_semantic_topology(tree, {}, selected_topology)
    if variant_state_bound(tree, search_budget, selected_topology) > search_budget:
        selected = _guided_search(
            tree,
            initial,
            measure_state,
            limits=limits,
            search_budget=search_budget,
            locked=locked,
            topology=None if semantic_topology is None else selected_topology,
        )
    else:
        selected = _bounded_search(
            tree,
            initial,
            measure_state,
            limits=limits,
            search_budget=search_budget,
            locked=locked,
            topology=None if semantic_topology is None else selected_topology,
        )
    if strict and selected.notes:
        raise LayoutOverflowError(selected.notes)
    return selected


def _guided_search(
    tree: list[Node],
    initial: dict[VariantPath, int],
    measure_state,
    *,
    limits: V2Limits,
    search_budget: int,
    locked: frozenset[str],
    topology: Mapping[str, int] | None,
) -> SolvedLayout:
    """Preserve priority and breadth while guiding an intractable product by component savings."""
    positions = initial
    explored: list[VariantTopology] = []
    states_explored = 0
    selected: SolvedLayout | None = None
    while states_explored < search_budget:
        measured = measure_state(positions)
        states_explored += 1
        current = semantic_topology(tree, positions)
        if current not in explored:
            explored.append(current)
        selected = SolvedLayout(
            measured,
            states_explored,
            bool(positions) or measured.components > limits.total_components,
            tuple(positions.items()),
            current,
            tuple(explored),
        )
        if measured.components <= limits.total_components and not measured.failures:
            break
        stepped = guided_step(tree, positions, limits, locked_semantics=locked, topology=topology)
        if stepped is None:
            break
        positions = stepped
    assert selected is not None
    return selected


def _bounded_search(
    tree: list[Node],
    initial: dict[VariantPath, int],
    measure_state,
    *,
    limits: V2Limits,
    search_budget: int,
    locked: frozenset[str],
    topology: Mapping[str, int] | None,
) -> SolvedLayout:
    frontier: list[tuple[DegradationProfile, int, dict[VariantPath, int]]] = []
    serial = count()
    heappush(frontier, (variant_profile(tree, initial), next(serial), initial))
    seen: set[frozenset[tuple[VariantPath, int]]] = {frozenset(initial.items())}
    explored: list[VariantTopology] = []
    best: SolvedLayout | None = None
    best_overflow: SolvedLayout | None = None
    states_explored = 0

    while frontier and states_explored < search_budget:
        structural, _order, positions = heappop(frontier)
        if best is not None and best.degradation < structural:
            break
        measured = measure_state(positions)
        states_explored += 1
        current = semantic_topology(tree, positions)
        if current not in explored:
            explored.append(current)
        candidate = SolvedLayout(
            measured,
            states_explored,
            variant_positions=tuple(positions.items()),
            variant_topology=current,
        )
        valid = measured.components <= limits.total_components and not measured.failures
        if valid and (best is None or measured.degradation < best.degradation):
            best = candidate
            if measured.degradation.lossless:
                break
        if best_overflow is None or (measured.components, measured.degradation) < (
            best_overflow.components,
            best_overflow.degradation,
        ):
            best_overflow = candidate
        if valid:
            continue

        for path, _ladder, rung in steppable(tree, positions, locked_semantics=locked):
            neighbor = canonical_positions(tree, {**positions, path: rung + 1})
            if topology is not None:
                neighbor = apply_semantic_topology(tree, neighbor, topology)
            key = frozenset(neighbor.items())
            if key in seen:
                continue
            seen.add(key)
            lower_bound = variant_profile(tree, neighbor)
            if best is not None and best.degradation < lower_bound:
                continue
            heappush(frontier, (lower_bound, next(serial), neighbor))

    selected = best or best_overflow
    assert selected is not None
    return replace(
        selected,
        states_explored=states_explored,
        search_fallback=bool(frontier) and states_explored >= search_budget,
        explored_variant_topologies=tuple(explored),
    )
