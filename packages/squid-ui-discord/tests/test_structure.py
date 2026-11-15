"""Structural degradation: entry drop priorities and Variants, the component-budget policy."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from squid_ui import ComponentsV2Target, Renderable, paragraph, scene
from squid_ui.authoring.presentations import Replacement, corresponds, losses, ref
from squid_ui.ext.discord import DISCORD_V2_DPY27, render_message
from squid_ui.ext.discord import (
    V2_LIMITS as LIMITS,
)
from squid_ui.ext.discord.planning import measure
from squid_ui.ext.discord.planning.measurement.model import MeasuredPanel, MeasuredText
from squid_ui.planning import SolveNoteCode, plan
from squid_ui.planning.allocation import CertifiedAllocation
from squid_ui.planning.joint_search import JointExhaustedError, JointInfeasibleError
from squid_ui.planning.pagination.navigation import default_nav
from squid_ui.planning.result import PlanResult
from squid_ui.primitives import (
    Alt,
    ControlGroup,
    Fidelity,
    Lines,
    LinkButton,
    Never,
    Paginate,
    Panel,
    Row,
    Text,
    Variant,
    Variants,
)
from squid_ui.primitives.nodes import Node
from squid_ui.selectors import query, select


def _rendered(solved) -> str:
    """Collect measured text nodes, descending through panels used by these fixtures."""
    parts: list[str] = []

    def walk(children) -> None:
        for child in children:
            if isinstance(child, MeasuredText):
                parts.append(child.content)
            elif isinstance(child, MeasuredPanel):
                walk(child.children)

    walk(solved.children)
    return "\n".join(parts)


def _planned(nodes: Sequence[Renderable[ComponentsV2Target]], **options) -> PlanResult[scene.ComponentsV2]:
    """Ladders are planner decisions, so structural behaviour is observed through plan()."""
    return plan(nodes, target=DISCORD_V2_DPY27, **options)


def _text(result: PlanResult[scene.ComponentsV2]) -> str:
    return "\n".join(match.node.content for match in query(result.scene, select(scene.Text)).all())


def _components(result: PlanResult[scene.ComponentsV2]) -> int:
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


def _selected_rungs(result: PlanResult[scene.ComponentsV2]) -> list[int]:
    assert isinstance(result.allocation, CertifiedAllocation)
    pending = [result.allocation.family.root]
    rungs = []
    while pending:
        part = pending.pop()
        if part.offer.key.startswith("rung-"):
            rungs.append(int(part.offer.key.removeprefix("rung-")))
        pending.extend(reversed(part.children))
    return rungs


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


def _text_ladder(*nodes: Node, priority: int = 0) -> Variants[Any]:
    """Declare equivalent prose shapes for tests of component allocation."""
    return Variants(
        tuple(Variant((node,), correspondence=(corresponds("@content", "source"),)) for node in nodes),
        content=paragraph("Fixture content", key="source"),
        priority=priority,
    )


def _ladder_document(count: int, *, priorities: list[int] | None = None) -> list[Variants[Any]]:
    """Four-component prose panels with an equivalent single-text presentation."""
    ranks = priorities if priorities is not None else [0] * count
    return [
        _text_ladder(
            Panel((Text(f"panel {index}"), Text("detail"), Text("more detail"))),
            Text(f"line {index}"),
            priority=rank,
        )
        for index, rank in enumerate(ranks)
    ]


class TestVariants:
    def test_a_document_that_fits_keeps_every_first_rung(self):
        result = _planned(_ladder_document(3))
        assert result.report.events == ()
        assert _components(result) == 3 * 4
        assert "panel 0" in _text(result)

    def test_stepping_brings_an_oversized_document_under_the_limit(self):
        result = _planned(_ladder_document(12))
        assert _components(result) <= LIMITS.total_components
        assert any(_selected_rungs(result))

    def test_it_steps_the_lowest_priority_ladders_first(self):
        rendered = _text(_planned(_ladder_document(12, priorities=list(range(12)))))
        assert "line 0" in rendered  # lowest priority: stepped
        assert "panel 11" in rendered  # highest priority: kept whole

    def test_stepping_stops_as_soon_as_the_document_fits(self):
        # 11 panels is 44 components; one step frees three, which is not enough for 40.
        assert sum(rung > 0 for rung in _selected_rungs(_planned(_ladder_document(11)))) == 2

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

        with pytest.raises(JointExhaustedError):
            _planned([hard, unrelated], search_budget=2)
        result = _planned([hard, unrelated])
        assert "plain" in _text(result)

    def test_reused_ladder_values_still_step_one_occurrence_at_a_time(self):
        shared = _ladder_document(1)[0]
        result = _planned([shared] * 11)
        assert sum(rung > 0 for rung in _selected_rungs(result)) == 2
        assert _text(result).count("line 0") == 2
        assert _text(result).count("panel 0") == 9

    def test_a_later_rung_can_expose_another_ladder(self):
        regular = _ladder_document(9, priorities=[10] * 9)
        inner = _text_ladder(
            Panel((Text("inner panel"), Text("detail"), Text("more detail"))),
            Text("inner line"),
        )
        outer = _text_ladder(
            Panel(tuple(Text(f"outer {index}") for index in range(5))),
            inner,
            priority=-1,
        )
        result = _planned([*regular, Text("filler"), outer])
        assert _components(result) <= LIMITS.total_components
        assert "inner line" in _text(result)
        assert sum(rung > 0 for rung in _selected_rungs(result)) == 2

    def test_pagination_controls_participate_in_the_ladder_budget(self):
        entries = tuple(f"entry {index}" for index in range(20))
        result = _planned(
            [*_ladder_document(9), Lines(entries, overflow=Paginate(key="entries", per=10))], nav=default_nav
        )
        assert [pager.pages for pager in result.scene.pagers] == [2]
        assert _components(result) <= LIMITS.total_components
        assert any(_selected_rungs(result))

    def test_strict_mode_accepts_a_required_step_to_an_exact_rung(self):
        """Stepping is not loss; a smaller faithful shape is exactly what strict asked for."""
        result = _planned(_ladder_document(11), strict=True)

        assert _components(result) <= LIMITS.total_components
        assert sum(rung > 0 for rung in _selected_rungs(result)) == 2

    def test_strict_mode_rejects_a_required_step_to_a_lossy_rung(self):
        lossy = [
            replace(
                ladder,
                variants=(
                    ladder.variants[0],
                    replace(ladder.variants[1], fidelity=Fidelity.LOSSY, contract=losses(Replacement(ref("source")))),
                ),
            )
            for ladder in _ladder_document(11)
        ]
        with pytest.raises(JointInfeasibleError):
            _planned(lossy, strict=True)


class TestVariantLadders:
    """Behaviour that only exists once a ladder can be longer than two rungs."""

    @staticmethod
    def _rungs(index: int) -> Variants:
        """A three-rung ladder costing 5, 3 and 1 components."""
        return _text_ladder(
            Panel(children=tuple(Text(f"p{index}.{step}") for step in range(4))),
            Panel(children=tuple(Text(f"h{index}.{step}") for step in range(2))),
            Text(f"line {index}"),
        )

    def test_equal_priority_ladders_step_breadth_first(self) -> None:
        # Nine ladders at 5 components each is 45; the limit is 40, and the first step of
        # each ladder frees two, so exactly three ladders reach their middle rung.
        result = _planned([self._rungs(index) for index in range(9)])
        rendered = _text(result)
        assert _components(result) <= LIMITS.total_components
        assert "h0.0" in rendered  # stepped once, not straight to the last rung
        # Three ladders take rung 1; none takes rung 2 while a sibling is still at rung 0.
        assert sum(f"h{index}.0" in rendered for index in range(9)) == 3
        assert not any(f"line {index}" in rendered for index in range(9))
        assert _selected_rungs(result) == [1, 1, 1, 0, 0, 0, 0, 0, 0]

    def test_priority_still_outranks_rung_depth(self):
        # Eight ladders at 5 plus four filler texts is 44 against a ceiling of 40, and each
        # step frees two — so the cheapest ladder is walked to its last rung before any
        # equal-priority sibling gives up its first.
        ladders = [self._rungs(index) for index in range(8)]
        ladders[0] = replace(ladders[0], priority=-10)
        filler = [Text(f"filler {index}") for index in range(4)]
        rendered = _text(_planned([*ladders, *filler]))
        assert "line 0" in rendered  # the low-priority ladder is exhausted first
        assert all(f"p{index}.0" in rendered for index in range(1, 8))

    def test_global_search_skips_an_equal_priority_step_that_saves_nothing(self) -> None:
        filler = [Text(f"filler {index}") for index in range(37)]
        ineffective = Variants.of(Text("a preferred"), Text("a alternate"))
        effective = _text_ladder(Panel((Text("b preferred"), Text("b detail"))), Text("b alternate"))

        result = _planned([*filler, ineffective, effective])
        rendered = _text(result)

        assert _components(result) <= LIMITS.total_components
        assert "a preferred" in rendered
        assert "a alternate" not in rendered
        assert "b alternate" in rendered
        assert result.metrics.target_evaluations > 0

    def test_variant_search_budget_returns_the_best_incumbent_and_reports_fallback(self) -> None:
        filler = [Text(f"filler {index}") for index in range(37)]
        ineffective = Variants.of(Text("a preferred"), Text("a alternate"))
        effective = _text_ladder(Panel((Text("b preferred"), Text("b detail"))), Text("b alternate"))

        with pytest.raises(JointExhaustedError) as error:
            _planned([*filler, ineffective, effective], search_budget=2)
        assert error.value.diagnostics.work_used <= 2


def test_a_bare_ladder_plans_to_its_first_rung() -> None:
    """The planner resolves ladders; a scene must never carry one."""
    result = _planned([Variants.of(Text("rich"), Text("plain"))])
    assert _text(result) == "rich"
    assert _components(result) == 1


def test_a_rung_may_lower_to_several_nodes() -> None:
    """A ControlGroup rung becomes one Row per five buttons, without a wrapper."""
    buttons = tuple(LinkButton(f"b{index}", "https://e.invalid") for index in range(8))

    # All buttons must survive each candidate; the alternative splits them into rows.
    ladder = Variants(
        (
            Variant((ControlGroup(buttons),)),
            Variant(
                (Row(buttons[:5]), Row(buttons[5:])),
                correspondence=tuple(
                    corresponds(ref("@content", f"@{index // 5}", f"@{index % 5}"), ref("@content", "@0", f"@{index}"))
                    for index in range(8)
                ),
            ),
        )
    )
    document = plan([ladder], target=DISCORD_V2_DPY27).scene
    # Two rows of five and three spliced in place, not wrapped in a Panel that would cost
    # the very container component the ladder exists to save.
    assert [len(child.items) for child in document.components_v2.children if isinstance(child, scene.Row)] == [5, 3]
    assert len(document.components_v2.children) == 2


@settings(deadline=None)
@given(st.integers(min_value=1, max_value=20))
def test_enough_steps_always_bring_the_document_within_limits(count: int) -> None:
    rendered = render_message(_ladder_document(count))
    assert _components(rendered.plan) <= LIMITS.total_components
