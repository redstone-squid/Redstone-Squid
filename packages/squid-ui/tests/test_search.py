"""Planner strategy costs are coarse, lexicographic, and deterministic."""

from squid_ui.planning.search import StrategyAxis, StrategyCandidate, choose_strategy, iter_assignments
from squid_ui.semantic import Flexibility


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


def test_global_assignments_advance_the_least_sacred_axis_first() -> None:
    axes = (
        StrategyAxis(
            "$.normal",
            "normal",
            "test",
            1,
            Flexibility.NORMAL,
            "new",
            (StrategyCandidate("old"), StrategyCandidate("new")),
            "old",
        ),
        StrategyAxis(
            "$.flexible",
            "flexible",
            "test",
            1,
            Flexibility.FLEXIBLE,
            "new",
            (StrategyCandidate("old"), StrategyCandidate("new")),
            "old",
        ),
    )

    assignments = list(iter_assignments(axes))

    assert [dict(assignment.strategies) for assignment in assignments] == [
        {"$.normal": "old", "$.flexible": "old"},
        {"$.normal": "old", "$.flexible": "new"},
        {"$.normal": "new", "$.flexible": "old"},
        {"$.normal": "new", "$.flexible": "new"},
    ]


def test_an_empty_strategy_product_has_one_assignment() -> None:
    assignments = list(iter_assignments(()))

    assert len(assignments) == 1
    assert assignments[0].strategies == ()
