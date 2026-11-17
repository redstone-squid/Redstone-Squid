"""Exercise convergence mechanics and read facts from a real certified allocation."""

from dataclasses import replace
from types import MappingProxyType

import pytest
from experiment import (
    Bodies,
    Budget,
    Deferred,
    FactContext,
    Facts,
    Membership,
    MembershipView,
    ResolutionError,
    Resolver,
    RowLayout,
)

import squid_ui as sl
import squid_ui.ext.html as html
from squid_ui.authoring.presentations import SemanticRef
from squid_ui.planning import plan
from squid_ui.planning.allocation import RegionPages
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.resources import EMPTY_COST


def footer(context: FactContext) -> str:
    return f"Page {context.read(('results', 'selection')) + 1} of {context.read(('results', 'count'))}"


def test_page_count_feedback_crosses_a_digit_boundary_and_stabilizes() -> None:
    result = Resolver(Budget(1000)).solve(
        (Deferred("footer", footer),),
        RowLayout(117, 20),
        snapshot="render-1",
        candidate="a",
    )
    assert result.bodies["footer"] == "Page 1 of 15"
    assert result.facts[("results", "count")] == 15
    assert result.rounds == 4
    assert result.callbacks == 3
    assert [step.reads[-1].value for step in result.trace] == [6, 13, 15]


def test_queries_reuse_unchanged_dependencies_but_follow_view_coordinates() -> None:
    nodes = (Deferred("footer", footer), Deferred("constant", lambda ctx: "fixed"))
    solver = Resolver(Budget(2000))
    first = solver.solve(nodes, RowLayout(117, 20), snapshot="r1", candidate="a")
    same = solver.solve(nodes, RowLayout(117, 20), snapshot="r1", candidate="a", prior=first)
    moved = solver.solve(nodes, RowLayout(117, 20, selected=1), snapshot="r1", candidate="a", prior=same)
    assert same.callbacks == 0
    assert moved.callbacks == 1
    assert moved.bodies["footer"] == "Page 2 of 15"
    assert moved.facts[("results", "count")] == 15
    assert first.bodies["footer"] == "Page 1 of 15"


@pytest.mark.parametrize(("snapshot", "candidate"), (("r2", "a"), ("r1", "b")))
def test_candidates_and_render_snapshots_do_not_borrow_cached_answers(snapshot: str, candidate: str) -> None:
    node = Deferred("footer", footer)
    solver = Resolver(Budget(2000))
    first = solver.solve((node,), RowLayout(117, 20), snapshot="r1", candidate="a")
    fresh = solver.solve((node,), RowLayout(117, 20), snapshot=snapshot, candidate=candidate, prior=first)
    assert fresh.callbacks == first.callbacks
    assert fresh.rounds == first.rounds


def test_pending_fact_can_be_produced_by_another_contextual_block() -> None:
    def layout(bodies: Bodies, budget: Budget) -> Facts:
        return {("target", "page"): 7} if bodies["producer"] else {}

    result = Resolver(Budget(100)).solve(
        (
            Deferred("producer", lambda ctx: "target"),
            Deferred("reference", lambda ctx: str(ctx.read(("target", "page")))),
        ),
        layout,
        snapshot="r1",
        candidate="a",
    )
    assert result.bodies["reference"] == "7"
    assert result.rounds == 3
    assert any(step.pending for step in result.trace)


def test_quiescent_missing_fact_fails_with_the_context_key_and_query() -> None:
    with pytest.raises(ResolutionError, match=r"reference.*target") as caught:
        Resolver(Budget(100)).solve(
            (Deferred("reference", lambda ctx: str(ctx.read(("target", "page")))),),
            lambda bodies, budget: {},
            snapshot="r1",
            candidate="a",
        )
    assert caught.value.reason == "unresolved required fact"


def test_oscillation_reports_the_values_that_caused_the_loop() -> None:
    def layout(bodies: Bodies, budget: Budget) -> Facts:
        return {("results", "count"): 1 if not bodies["switch"] else 2}

    with pytest.raises(ResolutionError) as caught:
        Resolver(Budget(100)).solve(
            (Deferred("switch", lambda ctx: "x" if ctx.read(("results", "count")) == 1 else ""),),
            layout,
            snapshot="r1",
            candidate="a",
        )
    assert caught.value.reason == "oscillation"
    assert [step.reads[0].value for step in caught.value.trace] == [1, 2]


def test_nonrepeating_growth_hits_a_round_limit() -> None:
    with pytest.raises(ResolutionError) as caught:
        Resolver(Budget(100), max_rounds=4).solve(
            (Deferred("grow", lambda ctx: "x" * (ctx.read(("self", "length")) + 1)),),
            lambda bodies, budget: {("self", "length"): len(bodies["grow"])},
            snapshot="r1",
            candidate="a",
        )
    assert caught.value.reason == "round limit"
    assert len(caught.value.trace) == 4


def test_layout_and_query_work_share_one_budget() -> None:
    budget = Budget(130)
    with pytest.raises(ResolutionError) as caught:
        Resolver(budget).solve((Deferred("footer", footer),), RowLayout(117, 20), snapshot="r1", candidate="a")
    assert caught.value.reason == "work exhausted"
    assert budget.used <= budget.limit


def test_real_allocator_metadata_answers_footer_summary_and_cross_reference() -> None:
    document = sl.bullets(*(sl.bullet(f"Build {i}", key=f"build-{i}") for i in range(83)), key="results", page_size=12)
    result = plan(document, target=html.target())
    allocation = result.allocation
    assert allocation is not None
    identity = next(iter(allocation.regions))
    before = allocation.select({identity: 0})
    view = MembershipView.capture(allocation, {identity: 1})
    assert view.region(identity).count == 7
    assert view.region(identity).selection == 1
    assert len(view.visible_sources) == 12
    source_order = allocation.regions[identity].source_order
    assert len(source_order) == 83
    assert view.locate(source_order[42], within=identity) == (Membership(identity, 3),)
    assert allocation.select({identity: 0}) is before


def test_fragmented_and_repeated_sources_are_not_collapsed_to_one_page() -> None:
    identity = OccurrenceId(("results",))
    source = SemanticRef(("results", "build"))
    region = RegionPages(
        "results",
        (1, 2),
        (frozenset({source}), frozenset({source})),
        EMPTY_COST,
        ((), ()),
    )
    view = MembershipView(MappingProxyType({identity: region}), {identity: 1})
    assert view.locate(source) == (Membership(identity, 0), Membership(identity, 1))
    assert len(view.visible_sources) == 1
    hidden = replace(view, selected={})
    assert hidden.region(identity).count == 2
    assert hidden.region(identity).selection is None
    assert not hidden.visible_sources
    assert not hidden.locate(SemanticRef(("absent",)))
