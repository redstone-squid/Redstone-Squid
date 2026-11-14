"""Shared accounting and construction contracts for explicitly selected candidates."""

from dataclasses import dataclass, replace
from typing import override

import pytest

import squid_ui as sl
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError, LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    CapacityExceeded,
    Evaluation,
    OccurrenceId,
    Placement,
    PlanningContext,
    RealizationOffer,
    ResourceIdentity,
    SemanticOccurrence,
    SharedResources,
    SharedUse,
    WorkBudget,
    build_composition,
)
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.planning.semantics import semantic_graph
from squid_ui.presentations import omit, ref
from squid_ui.target_types import RenderTarget


def test_shared_resources_are_counted_once_across_region_boundaries() -> None:
    asset = sl.file(b"schematic", filename="build.schem")
    use = SharedUse(ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1}))
    left = SharedResources.combine((use,))
    right = SharedResources.combine((use,))
    combined = SharedResources.combine((*left.uses.values(), *right.uses.values()))
    assert combined.cost == ResourceCost({Axis.ATTACHMENTS: 1})
    assert next(iter(combined.uses.values())).value is asset
    evaluation = Evaluation(additive_cost=ResourceCost({Axis.COMPONENTS: 2}), resources=combined)
    assert evaluation.cost == ResourceCost({Axis.COMPONENTS: 2, Axis.ATTACHMENTS: 1})


def test_same_count_does_not_make_resources_interchangeable() -> None:
    x, y = sl.file(b"x", filename="x.schem"), sl.file(b"y", filename="y.schem")
    use_x = SharedUse(ResourceIdentity("attachment", x.key), x, ResourceCost({Axis.ATTACHMENTS: 1}))
    use_y = SharedUse(ResourceIdentity("attachment", y.key), y, ResourceCost({Axis.ATTACHMENTS: 1}))
    assert SharedResources.combine((use_x,)).cost == SharedResources.combine((use_y,)).cost
    assert SharedResources.combine((use_x, use_x)).cost.get(Axis.ATTACHMENTS) == 1
    assert SharedResources.combine((use_x, use_y)).cost.get(Axis.ATTACHMENTS) == 2


def test_conflicting_resource_values_or_costs_are_invalid() -> None:
    asset = sl.file(b"x", filename="x.schem")
    use = SharedUse(ResourceIdentity("attachment", asset.key), asset, ResourceCost({Axis.ATTACHMENTS: 1}))
    for conflicting in (replace(use, value=replace(asset, name="other.schem")), replace(use, cost=ResourceCost())):
        with pytest.raises(LayoutInvariantError, match="conflicting declarations"):
            SharedResources.combine((use, conflicting))


@dataclass(frozen=True, slots=True)
class TextRules:
    limit: int = 20
    construction_suffix: str = ""

    def offers(
        self, occurrence: SemanticOccurrence[RenderTarget, object], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTarget, object, str], ...]:
        context.work.consume()
        return (RealizationOffer(occurrence, "literal", "hello"),)

    def evaluate(
        self, candidate: CandidateComposition[RenderTarget, object, str], context: PlanningContext
    ) -> Evaluation[Asset]:
        context.work.consume()
        length = len(candidate.offer.payload)
        violations = (
            (CapacityExceeded(candidate.offer.occurrence.identity, Axis.DISPLAY_TEXT, length, self.limit),)
            if length > self.limit
            else ()
        )
        return Evaluation(
            ResourceCost({Axis.DISPLAY_TEXT: length}), violations=violations, effects=candidate.offer.effects
        )

    def build(self, candidate: CandidateComposition[RenderTarget, object, str], context: PlanningContext) -> str:
        context.work.consume()
        return candidate.offer.payload + self.construction_suffix

    def validate(self, body: str, expected: Evaluation[Asset], context: PlanningContext) -> None:
        context.work.consume()
        if len(body) != expected.cost.get(Axis.DISPLAY_TEXT):
            message = "built text disagrees with evaluated cost"
            raise DrawInvariantError(message)


def test_one_budget_spans_offers_evaluation_construction_and_validation() -> None:
    rules, context = TextRules(), PlanningContext(WorkBudget(7))
    occurrence = SemanticOccurrence(
        OccurrenceId(("root",)), semantic_graph(sl.paragraph("hello", key="text")), ref("text")
    )
    (offer,) = rules.offers(occurrence, context)
    result = build_composition(CandidateComposition(offer), rules, context)
    assert isinstance(result, BuiltComposition)
    assert result.body == "hello"
    assert context.work.spent == 7
    with pytest.raises(LayoutSearchExhaustedError):
        rules.offers(occurrence, context)


def test_candidate_infeasibility_does_not_exhaust_or_build_the_search() -> None:
    rules, context = TextRules(limit=2, construction_suffix="must not build"), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(
        OccurrenceId(("root",)), semantic_graph(sl.paragraph("hello", key="text")), ref("text")
    )
    (offer,) = rules.offers(occurrence, context)
    result = build_composition(CandidateComposition(offer), rules, context)
    assert isinstance(result, Evaluation)
    assert not result.feasible
    assert context.work.spent == 3
    assert result.violations == (CapacityExceeded(occurrence.identity, Axis.DISPLAY_TEXT, 5, 2),)


def test_construction_mismatch_is_not_repaired_by_another_fitting_pass() -> None:
    rules, context = TextRules(construction_suffix="extra"), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(
        OccurrenceId(("root",)), semantic_graph(sl.paragraph("hello", key="text")), ref("text")
    )
    (offer,) = rules.offers(occurrence, context)
    with pytest.raises(DrawInvariantError, match="disagrees"):
        build_composition(CandidateComposition(offer), rules, context)
    assert context.work.spent == 7


def test_strict_admission_is_independent_of_preference() -> None:
    rules, context = TextRules(), PlanningContext(WorkBudget(100))
    occurrence = SemanticOccurrence(
        OccurrenceId(("root",)), semantic_graph(sl.paragraph("hello", key="text")), ref("text")
    )
    equivalent = RealizationOffer(occurrence, "equivalent", "hello", preference=100)
    lossy = replace(equivalent, key="lossy", effects=(omit("text"),), preference=0)
    assert isinstance(build_composition(CandidateComposition(equivalent), rules, context), BuiltComposition)
    rejected = build_composition(CandidateComposition(lossy), rules, context)
    assert isinstance(rejected, Evaluation) and not rejected.feasible


def test_repeated_semantics_require_distinct_placement_identity() -> None:
    graph = semantic_graph(sl.paragraph("Repeat", key="text"))
    header = SemanticOccurrence(OccurrenceId(("header",)), graph, ref("text"), Placement.HEADER)
    footer = SemanticOccurrence(OccurrenceId(("footer",)), graph, ref("text"), Placement.FOOTER)
    parent = SemanticOccurrence(OccurrenceId(("root",)), graph, ref("text"))
    offer = RealizationOffer(parent, "repeat", "", children=(header, footer))
    header_candidate = CandidateComposition(RealizationOffer(header, "text", "Repeat"))
    footer_candidate = CandidateComposition(RealizationOffer(footer, "text", "Repeat"))
    CandidateComposition(offer, (header_candidate, footer_candidate))
    with pytest.raises(LayoutInvariantError, match="declared occurrences"):
        CandidateComposition(offer, (header_candidate, header_candidate))


def test_target_cannot_introduce_undeclared_loss_in_nonstrict_mode() -> None:
    class LosingRules(TextRules):
        @override
        def evaluate(
            self, candidate: CandidateComposition[RenderTarget, object, str], context: PlanningContext
        ) -> Evaluation[Asset]:
            return replace(super().evaluate(candidate, context), effects=(omit("text"),))

    rules, context = LosingRules(), PlanningContext(WorkBudget(100), strict=False)
    occurrence = SemanticOccurrence(
        OccurrenceId(("root",)), semantic_graph(sl.paragraph("hello", key="text")), ref("text")
    )
    (offer,) = rules.offers(occurrence, context)
    with pytest.raises(DrawInvariantError, match="undeclared fidelity effect"):
        build_composition(CandidateComposition(offer), rules, context)


def test_child_cannot_reuse_a_placement_id_for_different_content() -> None:
    graph = semantic_graph(sl.stack(sl.paragraph("First", key="a"), sl.paragraph("Second", key="b"), key="root"))
    parent = SemanticOccurrence(OccurrenceId(("root",)), graph, ref("root"))
    child = SemanticOccurrence(OccurrenceId(("root", "child")), graph, ref("root", "a"))
    offer = RealizationOffer(parent, "container", "", children=(child,))
    substituted = replace(child, reference=ref("root", "b"))
    with pytest.raises(LayoutInvariantError, match="declared semantic source"):
        CandidateComposition(offer, (CandidateComposition(RealizationOffer(substituted, "text", "Second")),))
