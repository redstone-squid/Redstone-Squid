"""Deterministic lexicographic strategy selection without scalar weights."""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from heapq import heappop, heappush

from squid_ui.semantic import Flexibility

DEFAULT_SEARCH_BUDGET = 512
"""Layout states the planner's search evaluates before returning its best incumbent with `search_fallback` set."""


@dataclass(frozen=True, slots=True, order=True)
class CostVector:
    """The planner's tie-breaker below `DegradationProfile`, compared field by field in declaration order.

    A change to a `STABLE` axis outranks any number of `NORMAL` ones, and so on down; `path`
    and `strategy_id` make the order total, so equal-cost assignments still sort deterministically.
    """

    stable_changes: int = 0
    """Axes of `Flexibility.STABLE` whose selected strategy differs from their baseline."""
    normal_changes: int = 0
    flexible_changes: int = 0
    preference_mismatches: int = 0
    """Axes whose selected strategy is not the adapter's `preferred` one."""
    active_pagers: int = 0
    """Strategies that open a local pager."""
    transitions: int = 0
    """Summed `StrategyCandidate.transition_distance`."""
    path: str = ""
    strategy_id: str = ""


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """One selectable strategy of an axis, with the two costs `candidate_cost` cannot derive from the id alone."""

    strategy_id: str
    active_pagers: int = 0
    """Whether choosing this opens a local pager (0 or 1)."""
    transition_distance: int = 0
    """Index gap in the adapter's strategy order from the baseline, or from `preferred` without one."""


@dataclass(frozen=True, slots=True)
class StrategyAxis:
    """One semantic occurrence's finite, independently selectable strategies.

    Raises `ValueError` when `candidates` is empty or repeats a strategy id.
    """

    path: str
    key: str
    adapter_id: str
    adapter_version: int
    flexibility: Flexibility
    """How dearly a change from `baseline` is priced; see `CostVector`."""
    preferred: str
    """The adapter's first choice; any other selection costs one `preference_mismatch`."""
    candidates: tuple[StrategyCandidate, ...]
    baseline: str | None = None
    """The strategy the session remembers for `key`, or `None` when it has none or it is no longer offered."""

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
    """One strategy per axis, as `(path, strategy_id)` pairs in axis order, with the summed cost."""

    strategies: tuple[tuple[str, str], ...]
    cost: CostVector


@dataclass(frozen=True, slots=True)
class StrategyChoice:
    """What `choose_strategy` returns; `states_explored` is the number of candidates priced."""

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


def ranked_candidates(axis: StrategyAxis) -> tuple[tuple[CostVector, StrategyCandidate], ...]:
    """One axis's candidates, cheapest first, under the shared coarse tiers."""
    priced = [(candidate_cost(candidate, axis=axis), candidate) for candidate in axis.candidates]
    return tuple(sorted(priced, key=lambda item: item[0]))


def assignment_cost(axes: Sequence[StrategyAxis], strategies: Mapping[str, str]) -> CostVector:
    """Price one complete assignment the same way `iter_assignments` prices its own.

    `strategies` must map every axis's path to one of its candidate ids.
    """
    costs = [
        candidate_cost(next(item for item in axis.candidates if item.strategy_id == strategies[axis.path]), axis=axis)
        for axis in axes
    ]
    return CostVector(
        stable_changes=sum(item.stable_changes for item in costs),
        normal_changes=sum(item.normal_changes for item in costs),
        flexible_changes=sum(item.flexible_changes for item in costs),
        preference_mismatches=sum(item.preference_mismatches for item in costs),
        active_pagers=sum(item.active_pagers for item in costs),
        transitions=sum(item.transitions for item in costs),
        path="\0".join(axis.path for axis in axes),
        strategy_id="\0".join(strategies[axis.path] for axis in axes),
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
    """The cheapest candidate under `candidate_cost`. Raises `ValueError` when `candidates` is empty."""
    if not candidates:
        message = f"{path}: adapter produced no valid strategies"
        raise ValueError(message)
    axis = _axis_for_choice(candidates, path, flexibility, preferred, baseline)
    ranked = tuple((candidate_cost(candidate, axis=axis), candidate) for candidate in candidates)
    selected_cost, selected = min(ranked, key=lambda item: item[0])
    return StrategyChoice(selected, selected_cost, len(ranked))


def iter_assignments(axes: tuple[StrategyAxis, ...]) -> Iterator[StrategyAssignment]:
    """Yield every assignment of the axes' product once, in nondecreasing `CostVector` order.

    Expands one axis by one rank per step from a heap, so the product is never materialized.
    """
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
