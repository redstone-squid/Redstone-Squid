"""Structural degradation: entry drop priorities and Variants, the component-budget policy."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_layouts import plan
from squid_layouts.discord import (
    DEFAULT_LIMITS as LIMITS,
)
from squid_layouts.discord import (
    DEFAULT_TARGET,
    NavigationContext,
    default_nav,
    render_static,
)
from squid_layouts.planning import (
    LayoutOverflowError,
    SolveNoteCode,
    measure,
)
from squid_layouts.planning.measure import RPanel, RText
from squid_layouts.planning.solve import solve
from squid_layouts.primitives import (
    ActionGroup,
    Alt,
    Lines,
    LinkButton,
    Never,
    Node,
    Option,
    Paginate,
    Panel,
    Row,
    SelectMenu,
    Text,
    Variants,
)
from squid_layouts.scene.model import SceneRow


def _rendered(solved) -> str:
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
    return _rendered(measure([filler, Lines(entries)]))


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


def _ladder_document(count: int, *, priorities: list[int] | None = None) -> list[Node]:
    """`count` panels, each stepping to a single line of text.

    A panel here is 4 components (container, text, row, button) and its last rung is 1, so
    every step frees three.
    """
    ranks = priorities if priorities is not None else [0] * count
    return [
        Variants.of(
            Panel(children=(Text(f"panel {index}"), Row((LinkButton("open", "https://e.invalid"),)))),
            Text(f"line {index}"),
            priority=rank,
        )
        for index, rank in enumerate(ranks)
    ]


def _steps(solved) -> int:
    return sum(1 for note in solved.notes if note.code is SolveNoteCode.VARIANT_STEP)


class TestVariants:
    def test_a_document_that_fits_keeps_every_first_rung(self):
        solved = solve(_ladder_document(3))
        assert solved.notes == []
        assert solved.components == 3 * 4
        assert "panel 0" in _rendered(solved)

    def test_stepping_brings_an_oversized_document_under_the_limit(self):
        solved = solve(_ladder_document(12))
        assert solved.components <= LIMITS.total_components
        assert _steps(solved)

    def test_it_steps_the_lowest_priority_ladders_first(self):
        solved = solve(_ladder_document(12, priorities=list(range(12))))
        rendered = _rendered(solved)
        assert "line 0" in rendered  # lowest priority: stepped
        assert "panel 11" in rendered  # highest priority: kept whole

    def test_stepping_stops_as_soon_as_the_document_fits(self):
        # 11 panels is 44 components; one step frees three, which is not enough for 40.
        solved = solve(_ladder_document(11))
        assert _steps(solved) == 2

    def test_a_document_with_nothing_left_to_step_still_reports_the_overflow(self):
        panels = [
            Panel(children=(Text(f"panel {index}"), Row((LinkButton("open", "https://e.invalid"),))))
            for index in range(12)
        ]
        solved = solve(panels)
        assert any(note.code is SolveNoteCode.COMPONENT_BUDGET for note in solved.notes)

    def test_a_later_rung_can_resolve_a_hard_failure_without_component_pressure(self):
        solved = solve([Variants.of(Text("x" * 5000, overflow=Never()), Text("plain"))])

        assert _rendered(solved) == "plain"
        assert not solved.failures

    def test_the_bounded_fallback_can_resolve_a_hard_failure(self):
        hard = Variants.of(Text("x" * 5000, overflow=Never()), Text("plain"))
        unrelated = Variants.of(Text("preferred"), Text("alternate"))

        solved = solve([hard, unrelated], search_budget=2)

        assert "plain" in _rendered(solved)
        assert solved.search_fallback
        assert not solved.failures

    def test_reused_ladder_values_still_step_one_occurrence_at_a_time(self):
        shared = _ladder_document(1)[0]
        solved = solve([shared] * 11)
        assert _steps(solved) == 2
        assert _rendered(solved).count("line 0") == 2
        assert _rendered(solved).count("panel 0") == 9

    def test_a_later_rung_can_expose_another_ladder(self):
        regular = _ladder_document(9, priorities=[10] * 9)
        inner = Variants.of(
            Panel(children=(Text("inner panel"), Row((LinkButton("open", "https://e.invalid"),)))),
            Text("inner line"),
        )
        outer = Variants.of(
            Panel(children=tuple(_ladder_document(2))),
            Panel(children=(inner,)),
            priority=-1,
        )
        solved = solve([*regular, outer])
        assert solved.components <= LIMITS.total_components
        assert "inner line" in _rendered(solved)
        assert _steps(solved) == 2

    def test_pagination_controls_participate_in_the_ladder_budget(self):
        async def move(interaction) -> None: ...

        def nav(state):
            return default_nav(NavigationContext(state, move, move))

        entries = tuple(f"entry {index}" for index in range(20))
        solved = solve([*_ladder_document(9), Lines(entries, overflow=Paginate(per=10))], nav=nav)
        assert solved.pages == 2
        assert solved.components <= LIMITS.total_components
        assert _steps(solved)

    def test_strict_mode_rejects_a_required_step(self):
        with pytest.raises(LayoutOverflowError, match="stepped"):
            solve(_ladder_document(11), strict=True)


class TestVariantLadders:
    """Behaviour that only exists once a ladder can be longer than two rungs."""

    @staticmethod
    def _rungs(index: int) -> Variants:
        """A three-rung ladder costing 5, 3 and 1 components."""
        return Variants.of(
            Panel(children=tuple(Text(f"p{index}.{step}") for step in range(4))),
            Panel(children=tuple(Text(f"h{index}.{step}") for step in range(2))),
            Text(f"line {index}"),
        )

    def test_a_ladder_steps_one_rung_per_iteration(self):
        # Nine ladders at 5 components each is 45; the limit is 40, and the first step of
        # each ladder frees two, so exactly three ladders reach their middle rung.
        solved = solve([self._rungs(index) for index in range(9)])
        rendered = _rendered(solved)
        assert solved.components <= LIMITS.total_components
        assert "h0.0" in rendered  # stepped once, not straight to the last rung
        assert "line 0" not in rendered

    def test_equal_priority_ladders_step_breadth_first(self):
        solved = solve([self._rungs(index) for index in range(9)])
        rendered = _rendered(solved)
        # Three ladders take rung 1; none takes rung 2 while a sibling is still at rung 0.
        assert sum(f"h{index}.0" in rendered for index in range(9)) == 3
        assert not any(f"line {index}" in rendered for index in range(9))

    def test_priority_still_outranks_rung_depth(self):
        # Eight ladders at 5 plus four filler texts is 44 against a ceiling of 40, and each
        # step frees two — so the cheapest ladder is walked to its last rung before any
        # equal-priority sibling gives up its first.
        ladders = [self._rungs(index) for index in range(8)]
        ladders[0] = Variants.of(*ladders[0].variants, priority=-10)
        filler = [Text(f"filler {index}") for index in range(4)]
        solved = solve([*ladders, *filler])
        rendered = _rendered(solved)
        assert "line 0" in rendered  # the low-priority ladder is exhausted first
        assert all(f"p{index}.0" in rendered for index in range(1, 8))

    def test_the_note_names_the_stepped_ladder_and_its_rung(self):
        solved = solve([self._rungs(index) for index in range(9)])
        steps = [note for note in solved.notes if note.code is SolveNoteCode.VARIANT_STEP]
        assert steps[0].message == "$.0 stepped to variant 2 of 3 (priority 0) under layout pressure"
        assert [note.message.split()[0] for note in steps] == ["$.0", "$.1", "$.2"]

    def test_global_search_skips_an_equal_priority_step_that_saves_nothing(self) -> None:
        filler = [Text(f"filler {index}") for index in range(37)]
        ineffective = Variants.of(Text("a preferred"), Text("a alternate"))
        effective = Variants.of(Panel((Text("b preferred"), Text("b detail"))), Text("b alternate"))

        solved = solve([*filler, ineffective, effective])
        rendered = _rendered(solved)

        assert solved.components <= LIMITS.total_components
        assert "a preferred" in rendered
        assert "a alternate" not in rendered
        assert "b alternate" in rendered
        assert solved.states_explored == 3

    def test_variant_search_budget_returns_the_best_incumbent_and_reports_fallback(self) -> None:
        filler = [Text(f"filler {index}") for index in range(37)]
        ineffective = Variants.of(Text("a preferred"), Text("a alternate"))
        effective = Variants.of(Panel((Text("b preferred"), Text("b detail"))), Text("b alternate"))

        solved = solve([*filler, ineffective, effective], search_budget=2)

        assert solved.components <= LIMITS.total_components
        assert solved.states_explored == 2
        assert solved.search_fallback


def test_a_bare_ladder_solves_to_its_first_rung() -> None:
    """`solve()` resolves ladders itself; it must never leak one into the realized tree."""
    solved = solve([Variants.of(Text("rich"), Text("plain"))])
    assert _rendered(solved) == "rich"
    assert solved.components == 1
    assert all(not isinstance(child, Variants) for child in solved.children)


def test_a_rung_may_lower_to_several_nodes() -> None:
    """An ActionGroup rung becomes one Row per five buttons, spliced without a wrapper."""
    buttons = tuple(LinkButton(f"b{index}", "https://e.invalid") for index in range(8))

    async def choose(event) -> None: ...

    ladder = Variants.of(
        ActionGroup(buttons),
        SelectMenu(tuple(Option(f"b{index}", str(index)) for index in range(8)), choose, key="k"),
    )
    scene = plan([ladder], target=DEFAULT_TARGET).scene
    # Two rows of five and three spliced in place, not wrapped in a Panel that would cost
    # the very container component the ladder exists to save.
    assert [len(child.items) for child in scene.children if isinstance(child, SceneRow)] == [5, 3]
    assert len(scene.children) == 2


@given(st.integers(min_value=1, max_value=20))
def test_enough_steps_always_bring_the_document_within_limits(count):
    solved = solve(_ladder_document(count))
    assert solved.components <= LIMITS.total_components
    render_static(_ladder_document(count))
