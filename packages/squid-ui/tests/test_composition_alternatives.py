"""Checked presentations retain source meaning through target composition."""

from collections.abc import Mapping
from typing import Literal

import pytest

import squid_ui as sl
from squid_ui.assets import Asset
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    ConstraintViolation,
    Evaluation,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
    WorkBudget,
    build_composition,
)
from squid_ui.planning.composition_html import HtmlCompositionRules
from squid_ui.planning.composition_slack import SlackCompositionRules
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.realizations import Realization, TextRealization, select_composition
from squid_ui.planning.semantics import semantic_graph
from squid_ui.presentations import alternative, alternatives, equivalent, losses, present_text, ref, shorten
from squid_ui.target_types import Renderable, RenderTarget
from squid_ui.text import NEUTRAL, Localization


def compose[BodyT, PreparationT](
    node: Renderable[RenderTarget, object],
    rules: TargetRules[RenderTarget, object, Realization, Asset, BodyT, PreparationT],
    *,
    selections: Mapping[OccurrenceId, str] | None = None,
    strict: bool = True,
    localization: Localization = NEUTRAL,
) -> tuple[
    CandidateComposition[RenderTarget, object, Realization],
    BuiltComposition[Asset, BodyT, PreparationT] | Evaluation[Asset, PreparationT],
]:
    context = PlanningContext(WorkBudget(1000), localization=localization, strict=strict)
    graph = semantic_graph(node)
    root = next(key for key, unit in graph.units.items() if unit.parent is None)
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), graph, root)
    candidate = select_composition(occurrence, rules, context, selections=selections or {})
    assert isinstance(candidate, CandidateComposition)
    return candidate, build_composition(candidate, rules, context)


@pytest.mark.parametrize("target", ["v2", "slack", "html"])
def test_equivalent_combined_text_builds_on_each_target(target: Literal["v2", "slack", "html"]) -> None:
    source = sl.stack(sl.paragraph("3", key="width"), sl.paragraph("Java", key="edition"), key="source")
    group = alternatives(
        alternative(
            present_text(ref("width"), " / ", ref("edition"), key="summary"), key="compact", fidelity=equivalent()
        ),
        content=source,
        key="build",
    )
    match target:
        case "v2":
            candidate, result = compose(group, V2CompositionRules[RenderTarget, object]())
        case "slack":
            candidate, result = compose(group, SlackCompositionRules[RenderTarget, object]())
        case "html":
            candidate, result = compose(group, HtmlCompositionRules[RenderTarget, object]())
    assert isinstance(result, BuiltComposition)
    assert "".join(result.evaluation.text) == "3 / Java"
    text = candidate.children[0]
    assert text.offer.occurrence.sources == {ref("build", "source", "width"), ref("build", "source", "edition")}
    assert isinstance(text.offer.payload, TextRealization)
    assert text.offer.payload.provenance is not None


def test_shortening_is_localized_and_its_effect_is_scoped() -> None:
    operation = shorten("description", max_chars=4)
    group = alternatives(
        alternative(present_text(operation, key="summary"), key="short", fidelity=losses(operation)),
        content=sl.paragraph(sl.tr(t"description = Description"), key="description"),
        key="help",
    )
    rules = V2CompositionRules[RenderTarget, object]()
    candidate, result = compose(
        group, rules, strict=False, localization=Localization(locale="de", resources=("description = Hilfe",))
    )
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.text == ("Hilf",)
    assert result.evaluation.effects == (shorten(ref("help", "description"), max_chars=4),)
    payload = candidate.children[0].offer.payload
    assert isinstance(payload, TextRealization) and payload.provenance is not None
    assert payload.provenance.fragments[0].removed_chars == 1
    _, strict_result = compose(group, rules)
    assert isinstance(strict_result, Evaluation)
    assert strict_result.violations == (ConstraintViolation(OccurrenceId(("root",)), "strict preservation"),)


def test_nested_alternatives_keep_outer_source_identity() -> None:
    source = sl.paragraph("Original", key="body")
    inner = alternatives(
        alternative(sl.paragraph("Reworded", key="body"), key="rewrite", fidelity=equivalent()),
        content=source,
        key="inner",
    )
    outer = alternatives(alternative(inner, key="nested", fidelity=equivalent()), content=source, key="outer")
    candidate, result = compose(outer, V2CompositionRules[RenderTarget, object]())
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.text == ("Reworded",)
    assert candidate.children[0].children[0].offer.occurrence.sources == {ref("outer", "body")}


def test_sibling_alternatives_have_distinct_semantic_scopes() -> None:
    source = sl.paragraph("Body", key="body")
    choice = alternative(source, key="full", fidelity=equivalent())
    node = sl.stack(
        alternatives(choice, content=source, key="left"),
        alternatives(choice, content=source, key="right"),
        key="layout",
    )
    candidate, result = compose(node, V2CompositionRules[RenderTarget, object]())
    assert isinstance(result, BuiltComposition)
    left, right = (child.children[0].offer.occurrence.sources for child in candidate.children)
    assert left == {ref("layout", "left", "body")}
    assert right == {ref("layout", "right", "body")}


def test_alternative_order_does_not_change_selected_source_identity() -> None:
    source = sl.paragraph("Body", key="body")
    choices = (
        alternative(source, key="full", fidelity=equivalent()),
        alternative(sl.paragraph("Reworded", key="body"), key="compact", fidelity=equivalent()),
    )
    rules = V2CompositionRules[RenderTarget, object]()
    for ordered in (choices, tuple(reversed(choices))):
        candidate, result = compose(
            alternatives(*ordered, content=source, key="group"), rules, selections={OccurrenceId(("root",)): "compact"}
        )
        assert isinstance(result, BuiltComposition)
        assert result.evaluation.text == ("Reworded",)
        assert candidate.children[0].offer.occurrence.sources == {ref("group", "body")}


def test_unsupported_rich_shortening_rejects_a_candidate_without_flattening() -> None:
    operation = shorten("body", max_chars=1)
    group = alternatives(
        alternative(present_text(operation, key="summary"), key="short", fidelity=losses(operation)),
        content=sl.paragraph(sl.text_link("Docs", "https://example.com"), key="body"),
        key="group",
    )
    context = PlanningContext(WorkBudget(100))
    result = select_composition(
        SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(group), ref("group")),
        V2CompositionRules[RenderTarget, object](),
        context,
        selections={},
    )
    assert isinstance(result, UnsupportedCapability)
    assert result.occurrence == OccurrenceId(("root", "@presentation"))
