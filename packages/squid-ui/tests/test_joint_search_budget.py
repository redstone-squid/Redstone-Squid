from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import override

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError
from squid_ui.planning.allocation_policy import SearchCompletion
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    DiscoveryCompletion,
    Evaluation,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    WorkBudget,
)
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.joint_search import (
    JointExhaustedError,
    JointInfeasibleError,
    JointUnsupportedError,
    search_composition,
)
from squid_ui.planning.realizations import Realization
from squid_ui.planning.semantics import semantic_graph
from squid_ui.semantic import Paragraph
from squid_ui.target_types import RenderTarget


def occurrence(node: sl.Renderable[RenderTarget, object]) -> SemanticOccurrence[RenderTarget, object]:
    graph = semantic_graph(node)
    root = next(reference for reference, unit in graph.units.items() if unit.parent is None)
    return SemanticOccurrence(OccurrenceId(("root",)), graph, root)


def finish[R, B, P, C](built: BuiltComposition[R, B, P, C], context: PlanningContext) -> BuiltComposition[R, B, P, C]:
    context.work.consume()
    return built


@dataclass(frozen=True, slots=True)
class FaultRules(V2CompositionRules[RenderTarget, object]):
    stage: str = ""
    fail_at: int = 2
    calls: dict[str, int] = field(default_factory=dict)

    def checkpoint(self, stage: str, context: PlanningContext) -> None:
        self.calls[stage] = self.calls.get(stage, 0) + 1
        if self.stage == stage and self.calls[stage] == self.fail_at:
            context.work.consume(context.work.remaining + 1)

    @override
    def offers(
        self, occurrence: SemanticOccurrence[RenderTarget, object], context: PlanningContext
    ) -> tuple[RealizationOffer[RenderTarget, object, Realization], ...]:
        if isinstance(occurrence.graph.units[occurrence.reference].value, Paragraph):
            self.checkpoint("offers", context)
        return super().offers(occurrence, context)

    @override
    def evaluate(
        self, candidate: CandidateComposition[RenderTarget, object, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.ComponentsV2]:
        self.checkpoint("evaluate", context)
        return super().evaluate(candidate, context)

    @override
    def build(self, preparation: scene.ComponentsV2, context: PlanningContext) -> scene.ComponentsV2:
        self.checkpoint("build", context)
        if self.stage == "mismatch" and self.calls["build"] == self.fail_at:
            return replace(preparation, children=())
        return super().build(preparation, context)

    @override
    def validate(
        self, body: scene.ComponentsV2, expected: Evaluation[Asset, scene.ComponentsV2], context: PlanningContext
    ) -> None:
        self.checkpoint("validate", context)
        super().validate(body, expected, context)

    def finalize(
        self, built: BuiltComposition[Asset, scene.ComponentsV2, scene.ComponentsV2, object], context: PlanningContext
    ) -> BuiltComposition[Asset, scene.ComponentsV2, scene.ComponentsV2, object]:
        self.checkpoint("finalize", context)
        return finish(built, context)


def alternatives() -> sl.Renderable[RenderTarget, object]:
    source = sl.paragraph("original", key="text")
    return sl.alternatives(
        sl.alternative(source, key="a", fidelity=sl.equivalent()),
        sl.alternative(sl.paragraph("another", key="text"), key="b", fidelity=sl.equivalent()),
        content=source,
        key="choice",
    )


@pytest.mark.parametrize("stage", ["offers", "evaluate", "build", "validate", "finalize"])
@pytest.mark.parametrize("fail_at", [1, 2])
def test_exhaustion_at_each_stage_never_returns_an_uncertified_output(stage: str, fail_at: int) -> None:
    rules = FaultRules(stage=stage, fail_at=fail_at)
    context = PlanningContext(WorkBudget(10000))
    if fail_at == 1:
        with pytest.raises(JointExhaustedError) as caught:
            search_composition(occurrence(alternatives()), rules, context, finalize=rules.finalize)
        assert not caught.value.diagnostics.optimal
    else:
        result = search_composition(occurrence(alternatives()), rules, context, finalize=rules.finalize)
        assert result.diagnostics.completion == SearchCompletion.EXHAUSTED
        assert not result.diagnostics.optimal
        assert result.allocation.select().evaluation.text == ("original",)
        assert result.diagnostics.certified_combinations == 1


def test_construction_mismatch_propagates_even_with_a_valid_incumbent() -> None:
    rules = FaultRules(stage="mismatch")
    with pytest.raises(DrawInvariantError):
        search_composition(
            occurrence(alternatives()), rules, PlanningContext(WorkBudget(10000)), finalize=rules.finalize
        )


@dataclass(frozen=True, slots=True)
class DiscoveryRules:
    discover: Callable[
        [SemanticOccurrence[RenderTarget, object], PlanningContext], OfferDiscovery[RenderTarget, object, Realization]
    ]
    target: V2CompositionRules[RenderTarget, object] = V2CompositionRules()

    def offers(
        self, occurrence: SemanticOccurrence[RenderTarget, object], context: PlanningContext
    ) -> OfferDiscovery[RenderTarget, object, Realization]:
        return self.discover(occurrence, context)

    def evaluate(
        self, candidate: CandidateComposition[RenderTarget, object, Realization], context: PlanningContext
    ) -> Evaluation[Asset, scene.ComponentsV2]:
        return self.target.evaluate(candidate, context)

    def build(self, preparation: scene.ComponentsV2, context: PlanningContext) -> scene.ComponentsV2:
        return self.target.build(preparation, context)

    def validate(
        self, body: scene.ComponentsV2, expected: Evaluation[Asset, scene.ComponentsV2], context: PlanningContext
    ) -> None:
        self.target.validate(body, expected, context)


@pytest.mark.parametrize(
    "completion,error",
    [
        (DiscoveryCompletion.COMPLETE, JointInfeasibleError),
        (DiscoveryCompletion.UNSUPPORTED, JointUnsupportedError),
        (DiscoveryCompletion.EXHAUSTED, JointExhaustedError),
    ],
)
def test_empty_discovery_preserves_its_reason(completion: DiscoveryCompletion, error: type[Exception]) -> None:
    rules = DiscoveryRules(lambda occurrence, context: OfferDiscovery((), completion))
    with pytest.raises(error):
        search_composition(occurrence(sl.paragraph("Body")), rules, PlanningContext(WorkBudget(1000)), finalize=finish)


def test_partial_discovery_can_return_output_without_claiming_optimality() -> None:
    target = V2CompositionRules[RenderTarget, object]()
    rules = DiscoveryRules(
        lambda occurrence, context: OfferDiscovery(target.offers(occurrence, context), DiscoveryCompletion.EXHAUSTED)
    )
    result = search_composition(
        occurrence(sl.paragraph("Body")), rules, PlanningContext(WorkBudget(1000)), finalize=finish
    )
    assert result.allocation.select().evaluation.text == ("Body",)
    assert result.diagnostics.completion == SearchCompletion.EXHAUSTED
