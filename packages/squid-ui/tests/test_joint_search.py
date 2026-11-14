from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest

import squid_ui as sl
from squid_ui.planning.allocation_policy import AllocationGoal, RegionPolicy, SearchCompletion
from squid_ui.planning.composition import (
    BuiltComposition,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    WorkBudget,
)
from squid_ui.planning.composition_html import HtmlCompositionRules
from squid_ui.planning.composition_slack import SlackCompositionRules
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.joint_search import JointExhaustedError, JointInfeasibleError, search_composition
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import semantic_graph
from squid_ui.slack.target import SlackComponentLimits, SlackMessageLimits
from squid_ui.target_types import RenderTarget


def occurrence(node: sl.Renderable[RenderTarget, object]) -> SemanticOccurrence[RenderTarget, object]:
    graph = semantic_graph(node)
    root = next(reference for reference, unit in graph.units.items() if unit.parent is None)
    return SemanticOccurrence(OccurrenceId(("root",)), graph, root)


def finish[R, B, P, C](built: BuiltComposition[R, B, P, C], context: PlanningContext) -> BuiltComposition[R, B, P, C]:
    context.work.consume()
    return built


def test_joint_sibling_pages_are_certified_and_cursor_independent() -> None:
    node = sl.stack(
        *(
            sl.region(*(sl.paragraph(text, key=str(i)) for i, text in enumerate(values)), key=key, title=key)
            for key, values in (("left", ("a" * 12, "b" * 12, "c")), ("right", ("d" * 12, "e" * 12, "f")))
        ),
        key="root",
    )
    limits = V2Limits(total_text=47)
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](limits),
        PlanningContext(WorkBudget(100000), capacities=limits.capacities),
        finalize=finish,
    )
    assert result.diagnostics.optimal
    allocation = result.allocation
    left, right = (OccurrenceId(("root", key)) for key in ("left", "right"))
    assert len(allocation.family.regions[left].pages) >= 2
    assert len(allocation.family.regions[right].pages) >= 2
    for a, b in product(
        range(len(allocation.family.regions[left].pages)), range(len(allocation.family.regions[right].pages))
    ):
        built = allocation.select({left: a, right: b})
        assert built.evaluation.cost.within(limits.capacities)
    assert allocation.select({left: 999}) == allocation.select({left: len(allocation.family.regions[left].pages) - 1})


def test_native_table_keeps_presentation_across_pages() -> None:
    node = sl.region(
        sl.table(
            sl.columns(sl.column("Name", key="name")),
            *(sl.table_row(str(i), key=str(i)) for i in range(4)),
            key="table",
        ),
        key="region",
        title="Region",
    )
    limits = SlackMessageLimits(blocks=4, components=SlackComponentLimits(table_rows=3))
    result = search_composition(
        occurrence(node),
        SlackCompositionRules[RenderTarget, object](limits),
        PlanningContext(WorkBudget(100000), capacities=limits.capacities),
        finalize=finish,
    )
    assert result.diagnostics.optimal
    assert dict(result.allocation.selection)[OccurrenceId(("root", "table"))] == "table"
    assert len(result.allocation.family.regions[OccurrenceId(("root",))].pages) == 2
    assert all(page.output.evaluation.cost.within(limits.capacities) for page in result.allocation.pages)


def test_html_has_no_invented_global_message_budget() -> None:
    node = sl.region(*(sl.paragraph("long " * 1000, key=str(i)) for i in range(5)), key="region", title="Region")
    result = search_composition(
        occurrence(node),
        HtmlCompositionRules[RenderTarget, object](),
        PlanningContext(WorkBudget(10000)),
        finalize=finish,
    )
    assert result.diagnostics.optimal
    assert len(result.allocation.pages) == 1


def test_nested_regions_only_certify_visible_combinations() -> None:
    node = sl.region(
        sl.region(sl.paragraph("a"), sl.paragraph("b"), key="inner", title="Inner"),
        sl.paragraph("c"),
        key="outer",
        title="Outer",
    )
    outer, inner = OccurrenceId(("root",)), OccurrenceId(("root", "inner"))
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](),
        PlanningContext(WorkBudget(10000)),
        finalize=finish,
        partitions={outer: (1, 2), inner: (1, 2)},
    )
    assert len(result.allocation.pages) == 3
    assert set(dict(page.coordinates).get(inner) for page in result.allocation.pages) == {None, 0, 1}
    assert result.allocation.select({outer: 1, inner: 0}) is result.allocation.select({outer: 1, inner: 1})


def test_shared_resources_are_deduplicated_in_actual_visible_pages(tmp_path: Path) -> None:
    asset = sl.file(tmp_path / "unread.schem")
    node = sl.stack(
        *(sl.region(sl.download(asset, key="file"), sl.paragraph("text"), key=key, title=key) for key in ("a", "b")),
        key="root",
    )
    limits = V2Limits(attachments=1)
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](limits),
        PlanningContext(WorkBudget(10000), capacities=limits.capacities),
        finalize=finish,
    )
    assert result.diagnostics.optimal
    assert result.allocation.select().evaluation.cost.get(Axis.ATTACHMENTS) == 1


def test_hard_and_soft_allocation_minimums_are_distinct() -> None:
    node = sl.stack(*(sl.region(sl.paragraph("a"), key=key, title=key) for key in ("a", "b")), key="root")
    policies = {
        OccurrenceId(("root", key)): RegionPolicy(goals=(AllocationGoal(Axis.DISPLAY_TEXT, 10),)) for key in ("a", "b")
    }
    limits = V2Limits(total_text=15)
    context = PlanningContext(WorkBudget(10000), capacities=limits.capacities)
    result = search_composition(
        occurrence(node), V2CompositionRules[RenderTarget, object](limits), context, finalize=finish, policies=policies
    )
    assert result.nondominated[0].minimum_shortfalls == (9, 9)
    hard = {key: replace(value, goals=(replace(value.goals[0], hard=True),)) for key, value in policies.items()}
    with pytest.raises(JointInfeasibleError):
        search_composition(
            occurrence(node),
            V2CompositionRules[RenderTarget, object](limits),
            PlanningContext(WorkBudget(10000), capacities=limits.capacities),
            finalize=finish,
            policies=hard,
        )


def test_empty_region_is_valid_and_impossible_atom_is_infeasible() -> None:
    empty = search_composition(
        occurrence(sl.region(key="empty", title="Empty")),
        V2CompositionRules[RenderTarget, object](),
        PlanningContext(WorkBudget(1000)),
        finalize=finish,
    )
    assert len(empty.allocation.pages) == 1
    with pytest.raises(JointInfeasibleError):
        search_composition(
            occurrence(sl.region(sl.paragraph("x" * 100), key="r", title="R")),
            V2CompositionRules[RenderTarget, object](V2Limits(total_text=10)),
            PlanningContext(WorkBudget(10000)),
            finalize=finish,
        )


def test_budget_exhaustion_keeps_only_fully_certified_families() -> None:
    node = sl.region(*(sl.paragraph("x" * 10, key=str(i)) for i in range(6)), key="region", title="Region")
    rules = V2CompositionRules[RenderTarget, object](V2Limits(total_text=35))
    statuses: set[str] = set()
    for budget in (1, 100, 500, 1000, 3000, 10000, 50000):
        try:
            result = search_composition(occurrence(node), rules, PlanningContext(WorkBudget(budget)), finalize=finish)
        except JointExhaustedError as error:
            assert error.diagnostics.work_used <= budget
            statuses.add("no_result")
        else:
            statuses.add(result.diagnostics.completion)
            assert result.diagnostics.work_used <= budget
            assert all(page.output.evaluation.cost.within(rules.limits.capacities) for page in result.allocation.pages)
    assert {"no_result", SearchCompletion.EXHAUSTED, SearchCompletion.COMPLETE} <= statuses


def preferred_region(
    key: str, preferences: tuple[int, ...] = (0, 1, 2), *, reverse: bool = False
) -> sl.Renderable[RenderTarget, object]:
    values = ("a" * 9, "b" * 6, "c" * 3)
    choices = tuple(
        sl.alternative(sl.paragraph(value, key="text"), key=str(i), fidelity=sl.equivalent(), preference=preferences[i])
        for i, value in enumerate(values)
    )
    return sl.region(
        sl.alternatives(
            *(reversed(choices) if reverse else choices), content=sl.paragraph(values[0], key="text"), key="choice"
        ),
        key=key,
        title=key,
    )


@pytest.mark.parametrize("preferences", [(0, 1, 2), (0, 10, 20)])
@pytest.mark.parametrize("reverse", [False, True])
def test_fair_compromise_matches_independent_ordinal_oracle(preferences: tuple[int, ...], reverse: bool) -> None:
    node = sl.stack(
        preferred_region("a", preferences, reverse=reverse),
        preferred_region("b", preferences, reverse=reverse),
        key="root",
    )
    limits = V2Limits(total_text=12)
    rules = V2CompositionRules[RenderTarget, object](limits)
    result = search_composition(occurrence(node), rules, PlanningContext(WorkBudget(20000)), finalize=finish)
    feasible = {(a, b) for a, b in product(range(3), repeat=2) if (9, 6, 3)[a] + (9, 6, 3)[b] <= 12}
    expected = min(feasible, key=lambda ranks: (tuple(sorted(ranks, reverse=True)), ranks))
    choices = dict(result.allocation.selection)
    assert tuple(int(choices[OccurrenceId(("root", key, "choice"))]) for key in ("a", "b")) == expected == (1, 1)
    assert result.diagnostics.optimal
    # Explicitly fixed runs establish the feasible set independently of automatic
    # enumeration and its partition bound.
    observed = set()
    for a, b in product(range(3), repeat=2):
        selections = {OccurrenceId(("root", key, "choice")): str(value) for key, value in (("a", a), ("b", b))}
        try:
            search_composition(
                occurrence(node), rules, PlanningContext(WorkBudget(10000)), finalize=finish, selections=selections
            )
        except JointInfeasibleError:
            pass
        else:
            observed.add((a, b))
    assert observed == feasible


def test_explicit_region_priority_can_choose_first_and_third() -> None:
    node = sl.stack(preferred_region("a"), preferred_region("b"), key="root")
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](V2Limits(total_text=12)),
        PlanningContext(WorkBudget(20000)),
        finalize=finish,
        policies={OccurrenceId(("root", "a")): RegionPolicy(priority=1)},
    )
    choices = dict(result.allocation.selection)
    assert choices[OccurrenceId(("root", "a", "choice"))] == "0"
    assert choices[OccurrenceId(("root", "b", "choice"))] == "2"


@pytest.mark.parametrize("strict,preference,expected", [(True, 0, "full"), (False, 0, "short"), (False, 1, "full")])
def test_authorized_loss_preference_and_detail_ties(strict: bool, preference: int, expected: str) -> None:
    source = sl.stack(sl.paragraph("A", key="a"), sl.paragraph("B", key="b"), key="source")
    node = sl.alternatives(
        sl.alternative(source, key="full", fidelity=sl.equivalent(), preference=1),
        sl.alternative(
            sl.paragraph("A", key="a"), key="short", fidelity=sl.losses(sl.omit("b")), preference=preference
        ),
        content=source,
        key="choice",
    )
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](),
        PlanningContext(WorkBudget(10000), strict=strict),
        finalize=finish,
    )
    assert dict(result.allocation.selection)[OccurrenceId(("root",))] == expected


def test_incomparable_losses_remain_in_the_nondominated_set() -> None:
    source = sl.stack(sl.paragraph("A", key="a"), sl.paragraph("B", key="b"), key="source")
    node = sl.alternatives(
        sl.alternative(sl.paragraph("A", key="a"), key="a", fidelity=sl.losses(sl.omit("b"))),
        sl.alternative(sl.paragraph("B", key="b"), key="b", fidelity=sl.losses(sl.omit("a"))),
        content=source,
        key="choice",
    )
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](),
        PlanningContext(WorkBudget(10000), strict=False),
        finalize=finish,
    )
    assert len(result.nondominated) == 2
    assert dict(result.allocation.selection)[OccurrenceId(("root",))] == "a"


def test_partitions_match_exhaustive_multiaxis_oracle() -> None:
    lengths = ((90, 30, 70), (40, 80))
    node = sl.stack(
        *(
            sl.region(
                *(sl.paragraph("x" * size, key=str(i)) for i, size in enumerate(sizes)),
                key=str(region),
                title=str(region),
            )
            for region, sizes in enumerate(lengths)
        ),
        key="root",
    )
    limits = V2Limits(total_text=230, total_components=14)
    rules = V2CompositionRules[RenderTarget, object](limits)
    identities = tuple(OccurrenceId(("root", str(i))) for i in range(2))

    def partitions(size: int) -> list[tuple[int, ...]]:
        return [
            (*tuple(i + 1 for i, enabled in enumerate(mask) if enabled), size)
            for mask in product((False, True), repeat=size - 1)
        ]

    def page_costs(sizes: tuple[int, ...], cuts: tuple[int, ...]) -> list[tuple[int, int]]:
        return [
            (sum(sizes[start:end]) + (11 if len(cuts) > 1 else 0), end - start + (5 if len(cuts) > 1 else 0))
            for start, end in zip((0, *cuts[:-1]), cuts, strict=True)
        ]

    feasible: set[tuple[tuple[int, ...], ...]] = set()
    observed: set[tuple[tuple[int, ...], ...]] = set()
    for cuts in product(*(partitions(len(sizes)) for sizes in lengths)):
        if all(
            sum(cost[0] for cost in combination) <= limits.total_text
            and sum(cost[1] for cost in combination) <= limits.total_components
            for combination in product(*(page_costs(sizes, ends) for sizes, ends in zip(lengths, cuts, strict=True)))
        ):
            feasible.add(cuts)
        try:
            search_composition(
                occurrence(node),
                rules,
                PlanningContext(WorkBudget(20000)),
                finalize=finish,
                partitions=dict(zip(identities, cuts, strict=True)),
            )
        except JointInfeasibleError:
            pass
        else:
            observed.add(cuts)
    assert observed == feasible
    expected = min(feasible, key=lambda cuts: (sum(len(ends) - 1 for ends in cuts), cuts))
    result = search_composition(occurrence(node), rules, PlanningContext(WorkBudget(100000)), finalize=finish)
    assert tuple(result.allocation.family.regions[identity].cuts for identity in identities) == expected
    assert result.diagnostics.optimal


def test_character_hints_survive_wrappers_and_hard_budget_floors() -> None:
    node = sl.stack(*(sl.paragraph("x" * 4, key=str(i)) for i in range(4)), key="items").paged(
        key="r", chars=8, min_fill=7, widows=2
    )
    result = search_composition(
        occurrence(node),
        V2CompositionRules[RenderTarget, object](),
        PlanningContext(WorkBudget(100000)),
        finalize=finish,
    )
    assert result.allocation.family.regions[OccurrenceId(("root", "r"))].cuts == (2, 4)
    assert not any(result.nondominated[0].break_shortfalls)
    budgeted = sl.stack(
        sl.paragraph("x").budget(min=9, prefer=10), sl.paragraph("y").budget(min=9, prefer=10), key="root"
    )
    limits = V2Limits(total_text=17)
    with pytest.raises(JointInfeasibleError):
        search_composition(
            occurrence(budgeted),
            V2CompositionRules[RenderTarget, object](limits),
            PlanningContext(WorkBudget(10000), capacities=limits.capacities),
            finalize=finish,
        )
