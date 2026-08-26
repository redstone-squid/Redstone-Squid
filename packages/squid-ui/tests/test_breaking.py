"""Exact sequence fragmentation shared by text and heterogeneous regions."""

from squid_ui.planning.breaking import BreakItem, balanced_breaks


def test_component_pressure_participates_in_feasible_predecessor_windows() -> None:
    cuts = balanced_breaks(
        [BreakItem(10, components=2) for _ in range(6)],
        max_chars=100,
        max_components=5,
    )

    assert cuts == (2, 4, 6)


def test_forbidden_boundaries_and_widows_share_the_exact_objective() -> None:
    cuts = balanced_breaks(
        [
            BreakItem(30),
            BreakItem(10, break_after=False),
            BreakItem(30),
            BreakItem(10),
            BreakItem(10),
        ],
        max_chars=50,
        min_fill=30,
        widows=2,
    )

    assert cuts == (1, 3, 5)
