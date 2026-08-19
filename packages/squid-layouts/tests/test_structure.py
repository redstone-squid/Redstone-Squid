"""Structural degradation: entry drop priorities and Fold, the component-budget policy."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import (
    DEFAULT_CHROME,
    LIMITS,
    Alt,
    Fold,
    LayoutOverflowError,
    Lines,
    LinkButton,
    Node,
    PageContext,
    Paginate,
    Panel,
    Row,
    Text,
    assert_within_limits,
    conform,
    default_nav,
    materialize,
    solve,
)
from squid_layouts.solve import RPanel, RText, SolvedLayout


def _rendered(solved: SolvedLayout) -> str:
    """Every string the solved document would display, panels included."""
    parts: list[str] = []

    def walk(children) -> None:
        for child in children:
            if isinstance(child, RText):
                parts.append(child.content)
            elif isinstance(child, RPanel):
                walk(child.children)

    walk(solved.children)
    return "\n".join(parts)


def _under_pressure(entries: tuple[Alt | str, ...], *, spare: int) -> str:
    """Solve a Lines node with only ``spare`` characters left for it."""
    filler = Text("x" * (LIMITS.total_text - spare), priority=10)
    return _rendered(solve([filler, Lines(entries)]))


class TestEntryPriorities:
    def test_the_lowest_priority_entry_spills_first(self):
        entries = (
            Alt(primary="keep me", priority=10),
            Alt(primary="middling"),
            Alt(primary="expendable", priority=-10),
        )
        rendered = _under_pressure(entries, spare=20)
        assert "keep me" in rendered
        assert "expendable" not in rendered

    def test_ties_still_spill_from_the_tail(self):
        first = "first" * 4
        third = "third" * 4
        rendered = _under_pressure((first, "second" * 4, third), spare=55)
        assert first in rendered
        assert third not in rendered

    def test_a_plain_string_entry_is_priority_zero(self):
        rendered = _under_pressure((Alt(primary="urgent", priority=5), "ordinary" * 4), spare=25)
        assert "urgent" in rendered
        assert "ordinary" not in rendered

    def test_priority_does_not_reorder_what_survives(self):
        entries = (Alt(primary="tail", priority=10), Alt(primary="head", priority=-10), "middle")
        rendered = _under_pressure(entries, spare=4000)
        assert rendered.splitlines()[-3:] == ["tail", "head", "middle"]


def _folded_document(count: int, *, priorities: list[int] | None = None) -> list[Node]:
    """`count` panels, each folding to a single line of text.

    A panel here is 4 components (container, text, row, button) and its fallback is 1, so
    every fold frees three.
    """
    ranks = priorities if priorities is not None else [0] * count
    return [
        Fold(
            primary=Panel(children=(Text(f"panel {index}"), Row((LinkButton("open", "https://e.invalid"),)))),
            fallback=Text(f"line {index}"),
            priority=rank,
        )
        for index, rank in enumerate(ranks)
    ]


class TestFold:
    def test_a_document_that_fits_keeps_every_primary(self):
        solved = solve(_folded_document(3))
        assert solved.notes == []
        assert solved.components == 3 * 4
        assert "panel 0" in _rendered(solved)

    def test_folding_brings_an_oversized_document_under_the_limit(self):
        solved = solve(_folded_document(12))
        assert solved.components <= LIMITS.total_components
        assert any("folded" in note for note in solved.notes)

    def test_it_folds_the_lowest_priority_alternates_first(self):
        solved = solve(_folded_document(12, priorities=list(range(12))))
        rendered = _rendered(solved)
        assert "line 0" in rendered  # lowest priority: folded
        assert "panel 11" in rendered  # highest priority: kept whole

    def test_folding_stops_as_soon_as_the_document_fits(self):
        # 11 panels is 44 components; one fold frees three, which is not enough for 40.
        solved = solve(_folded_document(11))
        assert sum(1 for note in solved.notes if "folded" in note) == 2

    def test_a_document_with_nothing_left_to_fold_still_reports_the_overflow(self):
        panels = [
            Panel(children=(Text(f"panel {index}"), Row((LinkButton("open", "https://e.invalid"),))))
            for index in range(12)
        ]
        solved = solve(panels)
        assert any("exceed" in note for note in solved.notes)

    def test_reused_fold_values_still_collapse_one_occurrence_at_a_time(self):
        shared = _folded_document(1)[0]
        solved = solve([shared] * 11)
        assert sum("folded" in note for note in solved.notes) == 2
        assert _rendered(solved).count("line 0") == 2
        assert _rendered(solved).count("panel 0") == 9

    def test_a_fallback_can_expose_another_fold(self):
        regular = _folded_document(9, priorities=[10] * 9)
        inner = Fold(
            primary=Panel(children=(Text("inner panel"), Row((LinkButton("open", "https://e.invalid"),)))),
            fallback=Text("inner line"),
        )
        outer = Fold(
            primary=Panel(children=tuple(_folded_document(2))),
            fallback=Panel(children=(inner,)),
            priority=-1,
        )
        solved = solve([*regular, outer])
        assert solved.components <= LIMITS.total_components
        assert "inner line" in _rendered(solved)
        assert sum("folded" in note for note in solved.notes) == 2

    def test_pagination_controls_participate_in_the_fold_budget(self):
        async def move(interaction) -> None: ...

        def nav(key: str, page: int, pages: int):
            context = PageContext(key=key, page=page, pages=pages, on_prev=move, on_next=move)
            return default_nav(DEFAULT_CHROME)(context)

        entries = tuple(f"entry {index}" for index in range(20))
        solved = solve([*_folded_document(9), Lines(entries, overflow=Paginate(per=10))], nav=nav)
        assert solved.pages == 2
        assert solved.components <= LIMITS.total_components
        assert any("folded" in note for note in solved.notes)

    def test_strict_mode_rejects_a_required_fold(self):
        with pytest.raises(LayoutOverflowError, match="folded"):
            solve(_folded_document(11), strict=True)


@given(st.integers(min_value=1, max_value=20))
def test_enough_folds_always_bring_the_document_within_limits(count):
    solved = solve(_folded_document(count))
    view = materialize(solved)
    assert solved.components <= LIMITS.total_components
    assert conform(view) == []
    assert_within_limits(view)
