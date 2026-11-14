"""Selected controls retain live ownership without executing application code."""

from dataclasses import replace
from typing import Literal

import pytest

import squid_ui as sl
from squid_ui.assets import Asset
from squid_ui.capabilities import Capability
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
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
from squid_ui.planning.realizations import Realization, select_composition
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import semantic_graph
from squid_ui.presentations import alternative, alternatives, equivalent, losses, omit, ref
from squid_ui.target_types import Renderable, RenderTarget


async def forbidden(event: sl.PressEvent[object]) -> None:
    pytest.fail("planning called a press handler")


async def submitted(event: sl.forms.SubmitEvent[object]) -> None:
    pytest.fail("planning called a submit handler")


def compose[BodyT, PreparationT](
    node: Renderable[RenderTarget, object],
    rules: TargetRules[RenderTarget, object, Realization, Asset, BodyT, PreparationT],
    *,
    strict: bool = True,
) -> BuiltComposition[Asset, BodyT, PreparationT] | Evaluation[Asset, PreparationT]:
    context = PlanningContext(WorkBudget(1000), strict=strict, capabilities=frozenset({Capability.FORMS_MODAL}))
    graph = semantic_graph(node)
    root = next(ref for ref, unit in graph.units.items() if unit.parent is None)
    candidate = select_composition(
        SemanticOccurrence(OccurrenceId(("root",)), graph, root), rules, context, selections={}
    )
    assert isinstance(candidate, CandidateComposition)
    return build_composition(candidate, rules, context)


@pytest.mark.parametrize("target", ["v2", "slack", "html"])
def test_visible_controls_keep_handlers_and_source_references(target: Literal["v2", "slack", "html"]) -> None:
    control = sl.action_control("Open", forbidden, key="open")
    node = sl.action_controls(control, key="actions")
    match target:
        case "v2":
            result = compose(node, V2CompositionRules[RenderTarget, object]())
        case "slack":
            result = compose(node, SlackCompositionRules[RenderTarget, object]())
        case "html":
            result = compose(node, HtmlCompositionRules[RenderTarget, object]())
    assert isinstance(result, BuiltComposition)
    assert result.evaluation.interactions == {ref("actions", "open")}
    binding = next(iter(result.bindings.values()))
    assert binding.handler is forbidden
    assert binding.mode == control.mode
    assert binding.label == control.label


def test_controls_in_omitted_content_do_not_produce_live_bindings() -> None:
    first, second = (sl.action_control(name, forbidden, key=name) for name in ("first", "second"))
    content = sl.action_controls(first, second, key="actions")
    choice = alternative(sl.action_controls(second, key="actions"), key="one", fidelity=losses(omit("first")))
    result = compose(
        alternatives(choice, content=content, key="toolbar"), V2CompositionRules[RenderTarget, object](), strict=False
    )
    assert isinstance(result, BuiltComposition)
    assert len(result.bindings) == 1
    assert result.evaluation.interactions == {ref("toolbar", "actions", "second")}


def test_control_wire_id_follows_shared_semantics_across_presentation_containers() -> None:
    button = sl.action_control("Open", forbidden, key="open")
    source = sl.action_controls(button, key="original")
    rules = V2CompositionRules[RenderTarget, object]()
    keys: list[tuple[str, ...]] = []
    for container in ("one", "two"):
        group = alternatives(
            alternative(sl.action_controls(button, key=container), key="selected", fidelity=equivalent()),
            content=source,
            key="toolbar",
        )
        result = compose(group, rules)
        assert isinstance(result, BuiltComposition)
        keys.append(tuple(result.bindings))
    assert keys[0] == keys[1]


def test_form_trigger_retains_live_schema_and_submission_handler() -> None:
    spec = sl.forms.FormSpec("Edit", (sl.forms.TextField(key="name", label="Name"),))
    result = compose(sl.form("Edit", spec, key="edit", on_submit=submitted), V2CompositionRules[RenderTarget, object]())
    assert isinstance(result, BuiltComposition)
    form = next(iter(result.forms.values()))
    assert form.spec is spec and form.on_submit is submitted
    assert form.key in result.bindings
    assert result.evaluation.interactions == {ref("edit")}
    assert result.evaluation.cost.get(Axis.COMPONENTS) == 2


def test_slack_rejects_disabled_control_instead_of_enabling_it() -> None:
    button = sl.action_control("Open", forbidden, key="open")
    result = compose(
        sl.action_controls(replace(button, available=False), key="actions"),
        SlackCompositionRules[RenderTarget, object](),
    )
    assert isinstance(result, Evaluation)
    assert any(isinstance(failure, UnsupportedCapability) for failure in result.violations)


@pytest.mark.parametrize("routed", [False, True])
def test_region_page_ownership_is_not_invoked_or_rewritten(routed: bool) -> None:
    def route(page: int) -> str:
        pytest.fail("single-page composition must not call navigation factories")

    async def changed(event: sl.PageEvent[object]) -> None:
        pytest.fail("planning must not invoke page change handlers")

    owner = sl.RoutedPage(8, route) if routed else sl.controlled(8, changed)
    region = sl.region(sl.paragraph("All content"), key="region", title="Region", page=owner)
    result = compose(region, V2CompositionRules[RenderTarget, object]())
    assert isinstance(result, BuiltComposition)
    assert region.page is owner
    assert result.evaluation.text == ("All content",)
