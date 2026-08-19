"""Deterministic lexicographic strategy selection without scalar weights."""

from dataclasses import dataclass

from squid_layouts.semantic import Flexibility


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
class StrategyChoice:
    candidate: StrategyCandidate
    cost: CostVector
    states_explored: int


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

    def cost(candidate: StrategyCandidate) -> CostVector:
        changed = int(baseline is not None and candidate.strategy_id != baseline)
        return CostVector(
            stable_changes=changed if flexibility is Flexibility.STABLE else 0,
            normal_changes=changed if flexibility is Flexibility.NORMAL else 0,
            flexible_changes=changed if flexibility is Flexibility.FLEXIBLE else 0,
            preference_mismatches=int(candidate.strategy_id != preferred),
            active_pagers=candidate.active_pagers,
            transitions=candidate.transition_distance,
            path=path,
            strategy_id=candidate.strategy_id,
        )

    ranked = tuple((cost(candidate), candidate) for candidate in candidates)
    selected_cost, selected = min(ranked, key=lambda item: item[0])
    return StrategyChoice(selected, selected_cost, len(ranked))
