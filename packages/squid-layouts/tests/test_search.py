"""Planner strategy costs are coarse, lexicographic, and deterministic."""

from squid_layouts.planning.search import StrategyCandidate, choose_strategy
from squid_layouts.semantic import Flexibility


def test_sticky_baseline_beats_a_new_display_preference() -> None:
    choice = choose_strategy(
        (StrategyCandidate("individual"), StrategyCandidate("grouped")),
        path="$.actions",
        flexibility=Flexibility.NORMAL,
        preferred="individual",
        baseline="grouped",
    )

    assert choice.candidate.strategy_id == "grouped"


def test_fresh_session_uses_preference_before_pager_and_tie_break_tiers() -> None:
    choice = choose_strategy(
        (StrategyCandidate("paged", active_pagers=1), StrategyCandidate("grouped")),
        path="$.actions",
        flexibility=Flexibility.STABLE,
        preferred="grouped",
        baseline=None,
    )

    assert choice.candidate.strategy_id == "grouped"


def test_ties_break_by_path_then_strategy_id_without_weights() -> None:
    choice = choose_strategy(
        (StrategyCandidate("zeta"), StrategyCandidate("alpha")),
        path="$.actions",
        flexibility=Flexibility.FLEXIBLE,
        preferred="missing",
        baseline=None,
    )

    assert choice.candidate.strategy_id == "alpha"
    assert choice.states_explored == 2
