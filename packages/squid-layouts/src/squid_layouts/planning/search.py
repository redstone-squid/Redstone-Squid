"""Deterministic lexicographic strategy selection without scalar weights."""

from collections.abc import Iterator
from dataclasses import dataclass
from heapq import heappop, heappush

from squid_layouts.semantic import Flexibility

DEFAULT_SEARCH_BUDGET = 512


@dataclass(frozen=True, slots=True, order=True)
class CostVector:
    """Coarse planner tiers, ordered from sacred to cosmetic."""

    stable_changes: int = 0
    normal_changes: int = 0
    flexible_changes: int = 0
    preference_mismatches: int = 0
    active_pagers: int = 0
    transitions: int = 0
    path: str = ""
    strategy_id: str = ""


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    strategy_id: str
    active_pagers: int = 0
    transition_distance: int = 0


@dataclass(frozen=True, slots=True)
class StrategyAxis:
    """One semantic occurrence's finite, independently selectable strategies."""

    path: str
    key: str
    adapter_id: str
    adapter_version: int
    flexibility: Flexibility
    preferred: str
    candidates: tuple[StrategyCandidate, ...]
    baseline: str | None = None

    def __post_init__(self) -> None:
        strategies = tuple(candidate.strategy_id for candidate in self.candidates)
        if not strategies:
            message = f"{self.path}: adapter produced no valid strategies"
            raise ValueError(message)
        if len(set(strategies)) != len(strategies):
            message = f"{self.path}: adapter produced duplicate strategies"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class StrategyAssignment:
    """One complete assignment in global lexicographic order."""

    strategies: tuple[tuple[str, str], ...]
    cost: CostVector


@dataclass(frozen=True, slots=True)
class StrategyChoice:
    candidate: StrategyCandidate
    cost: CostVector
    states_explored: int


def candidate_cost(candidate: StrategyCandidate, *, axis: StrategyAxis) -> CostVector:
    """Price one candidate using the shared coarse planner tiers."""
    changed = int(axis.baseline is not None and candidate.strategy_id != axis.baseline)
    return CostVector(
        stable_changes=changed if axis.flexibility is Flexibility.STABLE else 0,
        normal_changes=changed if axis.flexibility is Flexibility.NORMAL else 0,
        flexible_changes=changed if axis.flexibility is Flexibility.FLEXIBLE else 0,
        preference_mismatches=int(candidate.strategy_id != axis.preferred),
        active_pagers=candidate.active_pagers,
        transitions=candidate.transition_distance,
        path=axis.path,
        strategy_id=candidate.strategy_id,
    )


def _axis_for_choice(
    candidates: tuple[StrategyCandidate, ...], path: str, flexibility: Flexibility, preferred: str, baseline: str | None
) -> StrategyAxis:
    return StrategyAxis(path, path, "", 0, flexibility, preferred, candidates, baseline)


def choose_strategy(
    candidates: tuple[StrategyCandidate, ...],
    *,
    path: str,
    flexibility: Flexibility,
    preferred: str,
    baseline: str | None,
) -> StrategyChoice:
    """Choose one valid candidate by stable lexicographic tiers and deterministic ties."""
    if not candidates:
        message = f"{path}: adapter produced no valid strategies"
        raise ValueError(message)
    axis = _axis_for_choice(candidates, path, flexibility, preferred, baseline)
    ranked = tuple((candidate_cost(candidate, axis=axis), candidate) for candidate in candidates)
    selected_cost, selected = min(ranked, key=lambda item: item[0])
    return StrategyChoice(selected, selected_cost, len(ranked))


def iter_assignments(axes: tuple[StrategyAxis, ...]) -> Iterator[StrategyAssignment]:
    """Enumerate a strategy product best-first without materializing the product."""
    ranked = tuple(
        tuple(
            sorted(
                ((candidate_cost(candidate, axis=axis), candidate) for candidate in axis.candidates),
                key=lambda item: item[0],
            )
        )
        for axis in axes
    )
    initial = (0,) * len(axes)
    frontier: list[tuple[CostVector, tuple[int, ...], StrategyAssignment]] = []
    seen = {initial}

    def assignment(indices: tuple[int, ...]) -> StrategyAssignment:
        ranked_choices = tuple(candidates[index] for candidates, index in zip(ranked, indices, strict=True))
        costs = tuple(cost for cost, _candidate in ranked_choices)
        choices = tuple(candidate for _cost, candidate in ranked_choices)
        cost = CostVector(
            stable_changes=sum(item.stable_changes for item in costs),
            normal_changes=sum(item.normal_changes for item in costs),
            flexible_changes=sum(item.flexible_changes for item in costs),
            preference_mismatches=sum(item.preference_mismatches for item in costs),
            active_pagers=sum(item.active_pagers for item in costs),
            transitions=sum(item.transitions for item in costs),
            path="\0".join(axis.path for axis in axes),
            strategy_id="\0".join(choice.strategy_id for choice in choices),
        )
        return StrategyAssignment(
            tuple((axis.path, choice.strategy_id) for axis, choice in zip(axes, choices, strict=True)),
            cost,
        )

    first = assignment(initial)
    heappush(frontier, (first.cost, initial, first))
    while frontier:
        _cost, indices, selected = heappop(frontier)
        yield selected
        for axis_index, candidates in enumerate(ranked):
            next_index = indices[axis_index] + 1
            if next_index >= len(candidates):
                continue
            neighbor = (*indices[:axis_index], next_index, *indices[axis_index + 1 :])
            if neighbor in seen:
                continue
            seen.add(neighbor)
            candidate = assignment(neighbor)
            heappush(frontier, (candidate.cost, neighbor, candidate))
