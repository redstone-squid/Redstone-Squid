from dataclasses import replace

from squid_ui.planning.allocation_policy import (
    AllocationQuality,
    PreferenceTree,
    compare_preferences,
    compare_quality,
    dense_ranks,
)
from squid_ui.presentations import omit


def siblings(*ranks: int) -> PreferenceTree:
    return PreferenceTree(children=tuple((0, PreferenceTree((rank,))) for rank in ranks))


def test_ordinal_fairness_and_explicit_priority() -> None:
    assert dense_ranks((0, 10, 20, 10)) == (0, 1, 2, 1)
    assert dense_ranks((2, 0, 1)) == (2, 0, 1)
    assert compare_preferences(siblings(1, 1), siblings(0, 2)) == -1
    assert compare_preferences(siblings(0, 2), siblings(2, 0)) == 0
    preferred = PreferenceTree(children=((1, PreferenceTree((0,))), (0, PreferenceTree((2,)))))
    compromise = PreferenceTree(children=((1, PreferenceTree((1,))), (0, PreferenceTree((1,)))))
    assert compare_preferences(preferred, compromise) == -1


def test_conditional_choices_nested_groups_and_zero_padding() -> None:
    assert compare_preferences(PreferenceTree((1,)), PreferenceTree((1, 0, 0))) == 0
    assert compare_preferences(PreferenceTree((1,)), PreferenceTree((1, 1))) == -1
    assert compare_preferences(PreferenceTree(), siblings(0, 0)) == 0
    assert (
        compare_preferences(
            PreferenceTree(children=((0, siblings(1, 1)),)), PreferenceTree(children=((0, siblings(0, 2)),))
        )
        == -1
    )


def test_minimums_preferences_fidelity_break_goals_then_turns() -> None:
    base = AllocationQuality((), siblings(0), frozenset(), (), 2, ())
    assert compare_quality(replace(base, minimum_shortfalls=(1,)), replace(base, preferences=siblings(1))) == 1
    assert compare_quality(replace(base, effects=frozenset({omit("a")})), replace(base, preferences=siblings(1))) == -1
    assert compare_quality(base, replace(base, effects=frozenset({omit("a")}))) == -1
    assert (
        compare_quality(
            replace(base, effects=frozenset({omit("a")})),
            replace(base, effects=frozenset({omit("b")}), turns=0),
        )
        is None
    )
    assert compare_quality(replace(base, break_shortfalls=(1,)), replace(base, turns=3)) == 1
    assert compare_quality(replace(base, turns=1), base) == -1
