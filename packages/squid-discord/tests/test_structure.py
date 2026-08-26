"""Structural degradation: entry drop priorities and Variants, the component-budget policy."""

from collections.abc import Sequence

import pytest
from hypothesis import given
from hypothesis import strategies as st

from squid_discord import DISCORD_V2_DPY27, render_static
from squid_discord import (
    V2_LIMITS as LIMITS,
)
from squid_layouts import scene
from squid_layouts.errors import LayoutDegradedError
from squid_layouts.planning import (
    SolveNoteCode,
    measure,
    plan,
)
from squid_layouts.planning.layout_measurement.model import RPanel, RText
from squid_layouts.planning.navigation import NavigationContext, default_nav
from squid_layouts.primitives import (
    ActionGroup,
    Alt,
    Fidelity,
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
    Variant,
    Variants,
)
from squid_layouts.scene.model import PlanResult


def _rendered(solved) -> str:
    """Every string the measured document would display, panels included."""
    parts: list[str] = []

    def walk(children) -> None:
        for child in children:
            if isinstance(child, RText):
                parts.append(child.content)
            elif isinstance(child, RPanel):
                walk(child.children)

    walk(solved.children)
    return "\n".join(parts)


def _planned(nodes: Sequence[Node], **options) -> PlanResult:
    """Ladders are planner decisions, so structural behaviour is observed through plan()."""
    return plan(nodes, target=DISCORD_V2_DPY27, **options)


def _text(result: PlanResult) -> str:
    parts: list[str] = []

    def walk(children: Sequence[scene.Node]) -> None:
        for child in children:
            if isinstance(child, scene.Text):
                parts.append(child.content)
            elif isinstance(child, scene.Panel):
                walk(child.children)
            elif isinstance(child, scene.Section):
                walk(child.texts)

    walk(result.scene.components_v2.children)
    return "\n".join(parts)


def _components(result: PlanResult) -> int:
    """What the drawn view will hold, counted the same way the planner budgets it."""

    def count(children: Sequence[scene.Node]) -> int:
        total = 0
        for child in children:
            match child:
                case scene.Panel(children=inner):
                    total += 1 + count(inner)
                case scene.Section(texts=texts):
                    total += 2 + len(texts)
                case scene.Row(items=items):
                    total += 1 + len(items)
                case scene.Select():
                    total += 2
                case _:
                    total += 1
        return total

    return count(result.scene.components_v2.children)


def _step_events(result: PlanResult) -> list[str]:
    return [event.message for event in result.report.events if event.code == f"layout.{SolveNoteCode.VARIANT_STEP}"]


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
        result = _planned(_ladder_document(3))
        assert result.report.events == ()
        assert _components(result) == 3 * 4
        assert "panel 0" in _text(result)

    def test_stepping_brings_an_oversized_document_under_the_limit(self):
        result = _planned(_ladder_document(12))
        assert _components(result) <= LIMITS.total_components
        assert _step_events(result)

    def test_it_steps_the_lowest_priority_ladders_first(self):
        rendered = _text(_planned(_ladder_document(12, priorities=list(range(12)))))
        assert "line 0" in rendered  # lowest priority: stepped
        assert "panel 11" in rendered  # highest priority: kept whole

    def test_stepping_stops_as_soon_as_the_document_fits(self):
        # 11 panels is 44 components; one step frees three, which is not enough for 40.
        assert len(_step_events(_planned(_ladder_document(11)))) == 2

    def test_a_document_with_nothing_left_to_step_still_reports_the_overflow(self):
        panels = [
            Panel(children=(Text(f"panel {index}"), Row((LinkButton("open", "https://e.invalid"),))))
            for index in range(12)
        ]
        measured = measure(panels)
        assert any(note.code is SolveNoteCode.COMPONENT_BUDGET for note in measured.notes)

    def test_a_later_rung_can_resolve_a_hard_failure_without_component_pressure(self):
        # plan() raises on an unresolved failure, so reaching a scene at all is the assertion.
        result = _planned([Variants.of(Text("x" * 5000, overflow=Never()), Text("plain"))])

        assert _text(result) == "plain"

    def test_the_bounded_fallback_can_resolve_a_hard_failure(self):
        hard = Variants.of(Text("x" * 5000, overflow=Never()), Text("plain"))
        unrelated = Variants.of(Text("preferred"), Text("alternate"))

        result = _planned([hard, unrelated], search_budget=2)

        assert "plain" in _text(result)
        assert result.metrics.search_fallback

    def test_reused_ladder_values_still_step_one_occurrence_at_a_time(self):
        shared = _ladder_document(1)[0]
        result = _planned([shared] * 11)
        assert len(_step_events(result)) == 2
        assert _text(result).count("line 0") == 2
        assert _text(result).count("panel 0") == 9

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
        result = _planned([*regular, outer])
        assert _components(result) <= LIMITS.total_components
        assert "inner line" in _text(result)
        assert len(_step_events(result)) == 2

    def test_pagination_controls_participate_in_the_ladder_budget(self):
        async def move(interaction) -> None: ...

        def nav(state):
            return default_nav(NavigationContext(state, move, move))

        entries = tuple(f"entry {index}" for index in range(20))
        result = _planned([*_ladder_document(9), Lines(entries, overflow=Paginate(key="entries", per=10))], nav=nav)
        assert [pager.pages for pager in result.scene.pagers] == [2]
        assert _components(result) <= LIMITS.total_components
        assert _step_events(result)

    def test_strict_mode_accepts_a_required_step_to_an_exact_rung(self):
        """Stepping is not loss; a smaller faithful shape is exactly what strict asked for."""
        result = _planned(_ladder_document(11), strict=True)

        assert _components(result) <= LIMITS.total_components
        assert len(_step_events(result)) == 2

    def test_strict_mode_rejects_a_required_step_to_a_lossy_rung(self):
        lossy = [
            Variants(
                (
                    Variant(
                        (Panel(children=(Text(f"panel {index}"), Row((LinkButton("open", "https://e.invalid"),)))),)
                    ),
                    Variant((Text(f"line {index}"),), fidelity=Fidelity.LOSSY),
                )
            )
            for index in range(11)
        ]

        with pytest.raises(LayoutDegradedError, match="stepped to lossy variant"):
            _planned(lossy, strict=True)


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
        result = _planned([self._rungs(index) for index in range(9)])
        rendered = _text(result)
        assert _components(result) <= LIMITS.total_components
        assert "h0.0" in rendered  # stepped once, not straight to the last rung
        assert "line 0" not in rendered

    def test_equal_priority_ladders_step_breadth_first(self):
        rendered = _text(_planned([self._rungs(index) for index in range(9)]))
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
        rendered = _text(_planned([*ladders, *filler]))
        assert "line 0" in rendered  # the low-priority ladder is exhausted first
        assert all(f"p{index}.0" in rendered for index in range(1, 8))

    def test_the_note_names_the_stepped_ladder_and_its_rung(self):
        steps = _step_events(_planned([self._rungs(index) for index in range(9)]))
        assert steps[0] == "$.0 stepped to variant 2 of 3 (priority 0) under layout pressure"
        assert [message.split()[0] for message in steps] == ["$.0", "$.1", "$.2"]

    def test_global_search_skips_an_equal_priority_step_that_saves_nothing(self) -> None:
        filler = [Text(f"filler {index}") for index in range(37)]
        ineffective = Variants.of(Text("a preferred"), Text("a alternate"))
        effective = Variants.of(Panel((Text("b preferred"), Text("b detail"))), Text("b alternate"))

        result = _planned([*filler, ineffective, effective])
        rendered = _text(result)

        assert _components(result) <= LIMITS.total_components
        assert "a preferred" in rendered
        assert "a alternate" not in rendered
        assert "b alternate" in rendered
        assert result.metrics.states_explored == 3

    def test_variant_search_budget_returns_the_best_incumbent_and_reports_fallback(self) -> None:
        filler = [Text(f"filler {index}") for index in range(37)]
        ineffective = Variants.of(Text("a preferred"), Text("a alternate"))
        effective = Variants.of(Panel((Text("b preferred"), Text("b detail"))), Text("b alternate"))

        result = _planned([*filler, ineffective, effective], search_budget=2)

        assert _components(result) <= LIMITS.total_components
        assert result.metrics.states_explored == 2
        assert result.metrics.search_fallback


def test_a_bare_ladder_plans_to_its_first_rung() -> None:
    """The planner resolves ladders; a scene must never carry one."""
    result = _planned([Variants.of(Text("rich"), Text("plain"))])
    assert _text(result) == "rich"
    assert _components(result) == 1


def test_a_rung_may_lower_to_several_nodes() -> None:
    """An ActionGroup rung becomes one Row per five buttons, spliced without a wrapper."""
    buttons = tuple(LinkButton(f"b{index}", "https://e.invalid") for index in range(8))

    async def choose(event) -> None: ...

    ladder = Variants.of(
        ActionGroup(buttons),
        SelectMenu(tuple(Option(f"b{index}", str(index)) for index in range(8)), choose, key="k"),
    )
    document = plan([ladder], target=DISCORD_V2_DPY27).scene
    # Two rows of five and three spliced in place, not wrapped in a Panel that would cost
    # the very container component the ladder exists to save.
    assert [len(child.items) for child in document.components_v2.children if isinstance(child, scene.Row)] == [5, 3]
    assert len(document.components_v2.children) == 2


@given(st.integers(min_value=1, max_value=20))
def test_enough_steps_always_bring_the_document_within_limits(count):
    assert _components(_planned(_ladder_document(count))) <= LIMITS.total_components
    render_static(_ladder_document(count))
