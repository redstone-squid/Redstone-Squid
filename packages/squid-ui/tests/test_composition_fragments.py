import pytest

import squid_ui as sl
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    TargetRules,
    WorkBudget,
    build_composition,
)
from squid_ui.planning.composition_html import HtmlCompositionRules
from squid_ui.planning.composition_slack import SlackCompositionRules
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.fragments import fragment_tree, slice_fragment
from squid_ui.planning.realizations import Realization, TableRealization, select_composition
from squid_ui.planning.semantics import semantic_graph
from squid_ui.presentations import ref
from squid_ui.target_types import RenderTarget


def check_table_fragments[ResourceT, BodyT, PreparationT](
    rules: TargetRules[RenderTarget, object, Realization, ResourceT, BodyT, PreparationT], form: str
) -> None:
    table = sl.table(
        sl.columns(sl.column("Name", key="name")),
        *(sl.table_row(f"Row {i}", key=str(i)) for i in range(3)),
        key="table",
    )
    context = PlanningContext(WorkBudget(1000))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(table), ref("table"))
    candidate = select_composition(occurrence, rules, context, selections={occurrence.identity: form})
    assert isinstance(candidate, CandidateComposition)
    structure = fragment_tree(candidate, context)
    fragments = [slice_fragment(structure, start, end, context) for start, end in ((0, 2), (2, 3))]
    for fragment in fragments:
        assert isinstance(fragment.offer.payload, TableRealization)
        built = build_composition(fragment, rules, context)
        assert isinstance(built, BuiltComposition)
        assert "Name" in " ".join(built.evaluation.text)
    a, b = (fragment.offer.occurrence.fragment for fragment in fragments)
    assert a is not None and b is not None
    assert a.sources.isdisjoint(b.sources)
    assert a.repeated == b.repeated == frozenset({ref("table", "columns", "name")})
    assert len(a.sources | b.sources) == 3
    assert fragments[0].offer.occurrence.identity != fragments[1].offer.occurrence.identity


def test_table_continuations_on_three_targets() -> None:
    check_table_fragments(V2CompositionRules[RenderTarget, object](), "records")
    check_table_fragments(SlackCompositionRules[RenderTarget, object](), "table")
    check_table_fragments(HtmlCompositionRules[RenderTarget, object](), "table")


def test_wrappers_preserve_legal_breaks_and_repeated_chrome() -> None:
    node = sl.region(
        sl.stack(sl.paragraph("A"), sl.paragraph("B")).unbreakable(),
        sl.paragraph("C").keep_with_next(),
        sl.paragraph("D"),
        sl.paragraph("E"),
        key="region",
        title="Region",
        header=(sl.paragraph("Header"),),
        footer=(sl.paragraph("Footer"),),
    )
    rules, context = V2CompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(1000))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("region"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    structure = fragment_tree(candidate, context)
    assert structure.legal_ends == (1, 3, 4)
    with pytest.raises(LayoutInvariantError, match="legal boundaries"):
        slice_fragment(structure, 0, 2, context)
    with pytest.raises(LayoutInvariantError, match="empty page"):
        slice_fragment(structure, 0, 0, context)
    for start, end, text in ((0, 1, ("A", "B")), (1, 3, ("C", "D")), (3, 4, ("E",))):
        fragment = slice_fragment(structure, start, end, context)
        built = build_composition(fragment, rules, context)
        assert isinstance(built, BuiltComposition)
        assert built.evaluation.text == ("Header", *text, "Footer")


def test_navigation_is_real_target_content() -> None:
    node = sl.region(sl.paragraph("Body"), key="region", title="Region")
    rules, context = SlackCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(1000))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("region"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition) and candidate.offer.region is not None
    chrome = candidate.offer.region.navigation(0, 2, context)
    assert len(chrome) == 2  # Slack cannot represent disabled buttons; the previous control is absent.
    for child in chrome:
        assert child.offer.occurrence.sources == frozenset()
        assert isinstance(build_composition(child, rules, context), BuiltComposition)
