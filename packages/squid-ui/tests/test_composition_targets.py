"""Composition targets evaluate full semantic candidates without legacy planners."""

from dataclasses import replace
from pathlib import Path

import pytest

import squid_ui as sl
from squid_i18n import tree
from squid_ui import scene
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    ConstraintViolation,
    Evaluation,
    LocalCapacityExceeded,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    UnsupportedCapability,
    WorkBudget,
    build_composition,
)
from squid_ui.planning.composition_html import HtmlCompositionRules
from squid_ui.planning.composition_slack import SlackCompositionRules
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.realizations import TableRealization, select_composition
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import semantic_graph
from squid_ui.presentations import ref
from squid_ui.semantic import TableDisplay
from squid_ui.slack.target import SlackComponentLimits, SlackMessageLimits
from squid_ui.target_types import RenderTarget


def test_v2_shares_an_unread_attachment_across_sibling_regions(tmp_path: Path) -> None:
    asset = sl.file(tmp_path / "not-fetched.schem")
    node = sl.stack(
        sl.region(sl.download(asset, key="file"), key="left", title="Left"),
        sl.region(sl.download(asset, key="file"), key="right", title="Right"),
        key="root",
    )
    rules = V2CompositionRules[RenderTarget, object](V2Limits(attachments=1))
    context = PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("root"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.cost.get(Axis.ATTACHMENTS) == 1
    assert result.evaluation.cost.get(Axis.COMPONENTS) == 2
    assert len(result.body.children) == 2


def test_v2_repeated_header_and_container_costs_remain_per_occurrence() -> None:
    node = sl.stack(
        *(
            sl.block(
                sl.region(
                    sl.paragraph("Body"),
                    header=(sl.paragraph("Header"),),
                    footer=(sl.paragraph("Footer"),),
                    key="region",
                    title="Region",
                ),
                key=key,
            )
            for key in ("a", "b")
        ),
        key="root",
    )
    rules, context = V2CompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("root"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.cost.get(Axis.COMPONENTS) == 8
    assert result.evaluation.text == ("Header", "Body", "Footer") * 2


def test_v2_checks_container_nesting_in_the_composed_context() -> None:
    node = sl.block(sl.block(sl.paragraph("Nested"), key="inner"), key="outer")
    rules, context = V2CompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("outer"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, Evaluation)
    assert result.violations == (ConstraintViolation(OccurrenceId(("root", "inner")), "nested V2 container"),)


def test_slack_native_overflow_is_a_candidate_failure_and_records_keep_every_cell() -> None:
    node = sl.table(
        sl.columns(*(sl.column(f"C{i}", key=f"c{i}") for i in range(3))),
        *(sl.table_row(*(f"r{row}c{column}" for column in range(3)), key=f"r{row}") for row in range(3)),
        key="table",
    )
    limits = SlackMessageLimits(components=SlackComponentLimits(table_rows=3, table_columns=2))
    rules, context = SlackCompositionRules[RenderTarget, object](limits), PlanningContext(WorkBudget(200))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("table"))
    native = select_composition(occurrence, rules, context, selections={occurrence.identity: "table"})
    assert isinstance(native, CandidateComposition)
    rejected = build_composition(native, rules, context)
    assert isinstance(rejected, Evaluation)
    assert {failure.limit for failure in rejected.violations if isinstance(failure, LocalCapacityExceeded)} == {
        "table.columns",
        "table.rows",
    }
    records = select_composition(occurrence, rules, context, selections={occurrence.identity: "records"})
    assert isinstance(records, CandidateComposition)
    assert isinstance(records.offer.payload, TableRealization)
    assert records.offer.payload.rows[2].cells[2].reference == ref("table", "rows", "r2", "c2")
    result = build_composition(records, rules, context)
    assert isinstance(result, BuiltComposition)
    assert len(result.body.blocks) == 3
    assert all(f"r{row}c{column}" in result.body.text for row in range(3) for column in range(3))
    assert all(f"C{column}: r{row}c{column}" in result.body.text for row in range(3) for column in range(3))
    assert result.evaluation.effects == ()


def test_slack_checks_aggregate_table_text_before_building() -> None:
    node = sl.table(sl.columns(sl.column("Label", key="label")), sl.table_row("Value", key="row"), key="table")
    rules = SlackCompositionRules[RenderTarget, object](
        SlackMessageLimits(components=SlackComponentLimits(table_text=8))
    )
    context = PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("table"))
    candidate = select_composition(occurrence, rules, context, selections={occurrence.identity: "table"})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, Evaluation)
    assert LocalCapacityExceeded(occurrence.identity, "table.text", 10, 8) in result.violations


def test_independent_validation_detects_changed_text_even_at_equal_cost() -> None:
    node = sl.paragraph("original", key="text")
    rules, context = V2CompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("text"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    evaluation = rules.evaluate(candidate, context)
    assert evaluation.preparation is not None
    body = rules.build(evaluation.preparation, context)
    text = body.children[0]
    assert isinstance(text, scene.Text)
    corrupt = replace(body, children=(replace(text, content="replaced"),))
    with pytest.raises(DrawInvariantError, match="disagrees with evaluation"):
        rules.validate(corrupt, evaluation, context)


def test_v2_validation_checks_container_metadata() -> None:
    node = sl.block(sl.paragraph("Content"), accent=0x112233, key="panel")
    rules, context = V2CompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("panel"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    evaluation = rules.evaluate(candidate, context)
    assert evaluation.preparation is not None
    body = rules.build(evaluation.preparation, context)
    panel = body.children[0]
    assert isinstance(panel, scene.Panel)
    with pytest.raises(DrawInvariantError, match="disagrees with evaluation"):
        rules.validate(replace(body, children=(replace(panel, accent=None),)), evaluation, context)


def test_slack_validation_preserves_cell_relationships_at_equal_text_and_cost() -> None:
    node = sl.table(
        sl.columns(sl.column("A", key="a"), sl.column("B", key="b")), sl.table_row("1", "2", key="r"), key="table"
    )
    rules, context = SlackCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("table"))
    candidate = select_composition(occurrence, rules, context, selections={occurrence.identity: "table"})
    assert isinstance(candidate, CandidateComposition)
    evaluation = rules.evaluate(candidate, context)
    assert evaluation.preparation is not None
    body = rules.build(evaluation.preparation, context)
    table = body.blocks[0]
    assert isinstance(table, scene.SlackTable)
    a, b, one, two = (cell for row in table.rows for cell in row)
    corrupt = replace(body, blocks=(replace(table, rows=((a,), (b, one, two))),))
    with pytest.raises(DrawInvariantError, match="disagrees with evaluation"):
        rules.validate(corrupt, evaluation, context)


def test_slack_builds_the_prepared_native_table_without_losing_rows() -> None:
    node = sl.table(
        sl.columns(sl.column("A", key="a"), sl.column("B", key="b")), sl.table_row("1", "2", key="r"), key="table"
    )
    rules, context = SlackCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("table"))
    candidate = select_composition(occurrence, rules, context, selections={occurrence.identity: "table"})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, BuiltComposition)
    assert result.body is result.evaluation.preparation
    table = result.body.blocks[0]
    assert isinstance(table, scene.SlackTable)
    assert tuple(tuple(cell.content for cell in row) for row in table.rows) == (("A", "B"), ("1", "2"))


def test_v2_constructs_and_validates_without_serializing_again(monkeypatch: pytest.MonkeyPatch) -> None:
    node = sl.paragraph("Content", key="text")
    rules, context = V2CompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("text"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    evaluated = rules.evaluate(candidate, context)
    assert evaluated.preparation is not None
    monkeypatch.setattr("squid_ui.planning.composition_v2._text", reject_reserialization)
    body = rules.build(evaluated.preparation, context)
    rules.validate(body, evaluated, context)
    assert body is evaluated.preparation


def test_slack_constructs_and_validates_without_serializing_again(monkeypatch: pytest.MonkeyPatch) -> None:
    node = sl.paragraph("Content", key="text")
    rules, context = SlackCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("text"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    evaluated = rules.evaluate(candidate, context)
    assert evaluated.preparation is not None
    monkeypatch.setattr("squid_ui.planning.composition_slack._plain", reject_reserialization)
    body = rules.build(evaluated.preparation, context)
    rules.validate(body, evaluated, context)
    assert body is evaluated.preparation


def reject_reserialization(nodes: tuple[tree.Node, ...]) -> str:
    raise AssertionError("construction and validation must consume prepared target text")


def test_html_has_no_implicit_message_size_limit() -> None:
    content = "a" * 20000
    node = sl.paragraph(content, key="text")
    rules, context = HtmlCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("text"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.text == (content,)
    assert result.evaluation.cost.axes == ()
    assert result.body is result.evaluation.preparation


def test_html_preserves_table_structure_and_source_references() -> None:
    node = sl.table(
        sl.columns(sl.column("A", key="a"), sl.column("B", key="b")), sl.table_row("1", "2", key="r"), key="table"
    )
    rules, context = HtmlCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("table"))
    candidate = select_composition(occurrence, rules, context, selections={occurrence.identity: "table"})
    assert isinstance(candidate, CandidateComposition)
    assert isinstance(candidate.offer.payload, TableRealization)
    assert candidate.offer.payload.rows[0].cells[1].reference == ref("table", "rows", "r", "b")
    result = build_composition(candidate, rules, context)
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.text == ("A", "B", "1", "2")
    table = result.body.children[0]
    assert isinstance(table, scene.HtmlElement) and table.tag == scene.HtmlTag.TABLE
    assert tuple(child.tag for child in table.children if isinstance(child, scene.HtmlElement)) == (
        scene.HtmlTag.THEAD,
        scene.HtmlTag.TBODY,
    )


def test_unbounded_html_refuses_unimplemented_authored_constraints() -> None:
    node = sl.paragraph("Too much for an authored budget", key="text").budget(min=0, prefer=3)
    rules, context = HtmlCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("@0"))
    rejected = select_composition(occurrence, rules, context, selections={})
    assert rejected == UnsupportedCapability(occurrence.identity, "Budgeted")


def test_html_retains_styled_links_and_shared_asset_references(tmp_path: Path) -> None:
    asset = sl.file(tmp_path / "not-fetched.schem")
    node = sl.stack(
        sl.paragraph(sl.strong(sl.text_link("Docs", "https://example.com"))),
        sl.download(asset, key="a"),
        sl.download(asset, key="b"),
        key="root",
    )
    rules, context = HtmlCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("root"))
    candidate = select_composition(occurrence, rules, context, selections={})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    assert isinstance(result, BuiltComposition)
    assert len(result.evaluation.resources.uses) == 1
    assert result.evaluation.text == ("Docs", asset.name, asset.name)
    paragraph = result.body.children[0]
    assert isinstance(paragraph, scene.HtmlElement)
    strong = paragraph.children[0]
    assert isinstance(strong, scene.HtmlElement) and strong.tag == scene.HtmlTag.STRONG
    link = strong.children[0]
    assert isinstance(link, scene.HtmlElement) and link.url == scene.HtmlUrlRef("https://example.com")


def test_representation_preference_does_not_forbid_lossless_alternatives() -> None:
    node = sl.table(
        sl.columns(sl.column("A", key="a")), sl.table_row("1", key="r"), key="table", display=TableDisplay.RECORDS
    )
    rules, context = HtmlCompositionRules[RenderTarget, object](), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("table"))
    offers = rules.offers(occurrence, context)
    assert {offer.key: offer.preference for offer in offers} == {"table": 1, "records": 0}
    candidate = select_composition(occurrence, rules, context, selections={occurrence.identity: "table"})
    assert isinstance(candidate, CandidateComposition)
    assert isinstance(build_composition(candidate, rules, context), BuiltComposition)
