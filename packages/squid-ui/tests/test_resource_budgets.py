"""Authored budgets use target resource counts without granting content loss."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Literal, override

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.errors import DrawInvariantError, LayoutInvariantError
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    CapacityExceeded,
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
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.realizations import Realization, select_composition
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.planning.semantics import semantic_graph
from squid_ui.presentations import budget, ref
from squid_ui.target_types import Renderable, RenderTarget
from squid_ui.text import NEUTRAL, Localization


def evaluate[BodyT, PreparationT](
    node: Renderable[RenderTarget, object],
    rules: TargetRules[RenderTarget, object, Realization, Asset, BodyT, PreparationT],
    *,
    localization: Localization = NEUTRAL,
    strict: bool = True,
    selections: Mapping[OccurrenceId, str] | None = None,
) -> Evaluation[Asset, PreparationT]:
    context = PlanningContext(WorkBudget(1000), localization=localization, strict=strict)
    occurrence = SemanticOccurrence(OccurrenceId(("root",)), semantic_graph(node), ref("@0"))
    candidate = select_composition(occurrence, rules, context, selections=selections or {})
    assert isinstance(candidate, CandidateComposition)
    result = build_composition(candidate, rules, context)
    return result.evaluation if isinstance(result, BuiltComposition) else result


def on_target(
    node: Renderable[RenderTarget, object], target: Literal["v2", "html", "slack"], *, strict: bool = True
) -> Evaluation[Asset, scene.ComponentsV2 | scene.HtmlBody | scene.SlackMessage]:
    match target:
        case "v2":
            return evaluate(node, V2CompositionRules[RenderTarget, object](), strict=strict)
        case "html":
            return evaluate(node, HtmlCompositionRules[RenderTarget, object](), strict=strict)
        case "slack":
            return evaluate(node, SlackCompositionRules[RenderTarget, object](), strict=strict)


@pytest.mark.parametrize("strict", [True, False])
def test_text_limit_rejects_without_authorizing_truncation(strict: bool) -> None:
    result = on_target(budget(sl.paragraph("four"), limits={Axis.DISPLAY_TEXT: 3}), "v2", strict=strict)
    assert result.violations == (CapacityExceeded(OccurrenceId(("root",)), Axis.DISPLAY_TEXT, 4, 3),)
    assert result.effects == ()
    assert result.text == ("four",)


def test_text_budget_uses_localized_protocol_text_including_markup_and_url() -> None:
    content = sl.strong(sl.text_link(sl.tr(t"budget-link = Docs"), "https://example.com/destination"))
    rules = V2CompositionRules[RenderTarget, object]()
    original = evaluate(budget(sl.paragraph(content), limits={}), rules)
    length = original.cost.get(Axis.DISPLAY_TEXT)
    assert length > 4
    node = budget(sl.paragraph(content), limits={Axis.DISPLAY_TEXT: length})
    assert evaluate(node, rules).feasible
    german = Localization(locale="de", resources=("budget-link = Hilfe",))
    rejected = evaluate(node, rules, localization=german)
    assert rejected.violations == (CapacityExceeded(OccurrenceId(("root",)), Axis.DISPLAY_TEXT, length + 1, length),)


def test_large_author_limit_cannot_relax_platform_capacity() -> None:
    node = budget(sl.paragraph("four"), limits={Axis.DISPLAY_TEXT: 100})
    result = evaluate(node, V2CompositionRules[RenderTarget, object](V2Limits(total_text=3)))
    assert result.violations == (CapacityExceeded(OccurrenceId(("root",)), Axis.DISPLAY_TEXT, 4, 3),)


def test_nested_budgets_check_each_scope_and_each_axis() -> None:
    node = budget(
        sl.stack(
            budget(sl.paragraph("abc", key="inner"), limits={Axis.DISPLAY_TEXT: 2}),
            sl.paragraph("tail", key="tail"),
            key="layout",
        ),
        limits={Axis.DISPLAY_TEXT: 6, Axis.COMPONENTS: 1},
    )
    result = on_target(node, "v2")
    assert result.scoped_costs[OccurrenceId(("root",))] == ResourceCost({Axis.DISPLAY_TEXT: 7, Axis.COMPONENTS: 2})
    assert set(result.violations) == {
        CapacityExceeded(OccurrenceId(("root",)), Axis.DISPLAY_TEXT, 7, 6),
        CapacityExceeded(OccurrenceId(("root",)), Axis.COMPONENTS, 2, 1),
        CapacityExceeded(OccurrenceId(("root", "layout", "@0")), Axis.DISPLAY_TEXT, 3, 2),
    }


def test_slack_representation_changes_block_usage() -> None:
    node = budget(
        sl.table(
            sl.columns(sl.column("Label", key="label")),
            sl.table_row("a", key="a"),
            sl.table_row("b", key="b"),
            key="table",
        ),
        limits={Axis.BLOCKS: 1},
    )
    rules = SlackCompositionRules[RenderTarget, object]()
    native = evaluate(node, rules, selections={OccurrenceId(("root", "table")): "table"})
    records = evaluate(node, rules, selections={OccurrenceId(("root", "table")): "records"})
    assert native.feasible and native.cost.get(Axis.BLOCKS) == 1
    assert records.violations == (CapacityExceeded(OccurrenceId(("root",)), Axis.BLOCKS, 2, 1),)


def test_shared_attachments_count_once_per_scope_even_after_a_sibling_uses_them() -> None:
    asset = sl.file(b"data", filename="report.txt")
    node = budget(
        sl.stack(
            sl.download(asset, key="outside"),
            budget(
                sl.stack(sl.download(asset, key="first"), sl.download(asset, key="second"), key="inside"),
                limits={Axis.ATTACHMENTS: 0},
            ),
            key="layout",
        ),
        limits={Axis.ATTACHMENTS: 1, Axis.COMPONENTS: 3},
    )
    result = on_target(node, "v2")
    assert result.cost.get(Axis.ATTACHMENTS) == 1
    assert result.cost.get(Axis.COMPONENTS) == 3
    assert result.violations == (CapacityExceeded(OccurrenceId(("root", "layout", "@1")), Axis.ATTACHMENTS, 1, 0),)


@pytest.mark.parametrize("target", ["v2", "html", "slack"])
def test_empty_budget_adds_no_constraints(target: Literal["v2", "html", "slack"]) -> None:
    assert on_target(budget(sl.paragraph("x" * 300), limits={}), target).feasible


@pytest.mark.parametrize("target", ["html", "slack"])
def test_unsupported_axis_is_rejected_even_with_zero_usage(target: Literal["html", "slack"]) -> None:
    result = on_target(budget(sl.paragraph(""), limits={Axis.DISPLAY_TEXT: 0}), target)
    assert result.violations == (UnsupportedCapability(OccurrenceId(("root",)), "resource budget: display_text"),)


def test_supported_axis_with_zero_usage_passes_a_zero_limit() -> None:
    result = on_target(budget(sl.paragraph("abc"), limits={Axis.ATTACHMENTS: 0}), "v2")
    assert result.feasible
    assert result.scoped_costs[OccurrenceId(("root",))].get(Axis.ATTACHMENTS) == 0


def test_physical_containers_count_toward_component_budget() -> None:
    result = on_target(budget(sl.block(sl.paragraph("abc")), limits={Axis.COMPONENTS: 1}), "v2")
    assert result.violations == (CapacityExceeded(OccurrenceId(("root",)), Axis.COMPONENTS, 2, 1),)


def test_budget_copies_its_limits_and_rejects_negative_values() -> None:
    limits = {Axis.COMPONENTS: 1}
    node = budget(sl.paragraph("abc"), limits=limits)
    limits[Axis.COMPONENTS] = 0
    assert on_target(node, "v2").feasible
    with pytest.raises(LayoutInvariantError, match="cannot be negative"):
        budget(sl.paragraph("abc"), limits={Axis.COMPONENTS: -1})


def test_target_cannot_certify_a_supported_budget_without_measuring_its_scope() -> None:
    class ForgetfulRules(V2CompositionRules[RenderTarget, object]):
        @override
        def evaluate(
            self, candidate: CandidateComposition[RenderTarget, object, Realization], context: PlanningContext
        ) -> Evaluation[Asset, scene.ComponentsV2]:
            return replace(super().evaluate(candidate, context), scoped_costs={})

    with pytest.raises(DrawInvariantError, match="did not measure resources"):
        evaluate(budget(sl.paragraph("abc"), limits={Axis.DISPLAY_TEXT: 3}), ForgetfulRules())


def test_rejected_budget_never_constructs_the_candidate() -> None:
    class BuildMustNotRun(V2CompositionRules[RenderTarget, object]):
        @override
        def build(self, preparation: scene.ComponentsV2, context: PlanningContext) -> scene.ComponentsV2:
            pytest.fail("a rejected composition must not construct a body")

    result = evaluate(budget(sl.paragraph("abc"), limits={Axis.COMPONENTS: 0}), BuildMustNotRun())
    assert not result.feasible
