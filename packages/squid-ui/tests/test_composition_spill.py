"""Portable spill and best-effort permissions preserve controls and expose content loss."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.limits import ClassicLimits, EmbedLimits, V2Limits
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantics import check_alternative
from squid_ui.planning.target import Target
from squid_ui.semantic import Importance, ListItem
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "spill.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "spill.slack", "1")
SLACK_TARGETS: list[Target[Any, Any, Any, Any]] = [
    sl.slack.message_target(adapter=SLACK),
    sl.slack.home_target(adapter=SLACK),
]
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    sl.html.target(),
    *SLACK_TARGETS,
]
SMALL_TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD, limits=V2Limits(total_text=9)),
    classic_target(DISCORD, limits=ClassicLimits(embeds=EmbedLimits(field_value=4))),
    sl.html.target(),
    *(
        replace(target, limits=replace(target.limits, components=replace(target.limits.components, section_text=10)))
        for target in SLACK_TARGETS
    ),
]
CHROME = Chrome(and_n_more=lambda count: f"+{count}")


@pytest.mark.parametrize("target", SMALL_TARGETS, ids=lambda target: target.id)
def test_spill_keeps_the_higher_priority_field_and_counts_its_notice(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.spill(
        sl.fields(
            sl.field("H", "high", key="high", importance=Importance.HIGH),
            sl.field("L", "low value too long", key="low", importance=Importance.LOW),
        )
    )
    constrained = sl.presentations.budget(node, limits={Axis.DISPLAY_TEXT: 9}) if target.id == "html.semantic" else node
    result = plan(constrained, target=target, chrome=CHROME)
    wire = scene.Codec.dumps(result.scene)
    assert "high" in wire and "low value" not in wire and "+1" in wire
    assert [(event.message, event.path.split("/")[-1]) for event in result.report.events] == [("Omission", "low")]
    with pytest.raises(JointInfeasibleError):
        plan(constrained, target=target, chrome=CHROME, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_spilled_list_keeps_original_ordered_number_and_source(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.spill(
        sl.bullets(
            ListItem("low", "Low", importance=Importance.LOW),
            ListItem("high", "High", importance=Importance.HIGH),
            key="rows",
            ordered=True,
        )
    )
    options = CompositionOptions(automatic=True, selections={OccurrenceId(("root", "@0", "rows")): "spill-1"})
    result = plan(node, target=target, composition=options, chrome=CHROME)
    wire = scene.Codec.dumps(result.scene)
    assert "High" in wire and "Low" not in wire and "+1" in wire
    assert [event.path.split("/")[-1] for event in result.report.events] == ["low"]
    assert result.allocation is not None
    if isinstance(result.scene.body, scene.HtmlBody):
        assert 'value="2"' in sl.html.Renderer().draw(result.scene, plan=result)
    else:
        assert "2. High" in wire


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_best_effort_preserves_control_bindings(target: Target[Any, Any, Any, Any]) -> None:
    async def click(event: sl.PressEvent) -> None:
        pytest.fail("planning invoked a callback")

    node = sl.best_effort(
        sl.group(
            sl.paragraph("Body"),
            sl.action_controls(sl.action_control("Keep control", click, key="keep"), key="actions"),
        )
    )
    result = plan(node, target=target, strict=True)
    assert result.bindings["keep"].handler is click
    assert "Keep control" in scene.Codec.dumps(result.scene)
    assert not result.report.events


def test_best_effort_can_relax_a_budget_floor_without_claiming_content_loss() -> None:
    target = components_v2_target(DISCORD, limits=V2Limits(total_text=4))
    plain = sl.budget(sl.paragraph("Fits"), min=8, prefer=8)
    options = CompositionOptions(automatic=True)
    with pytest.raises(JointInfeasibleError):
        plan(plain, target=target, composition=options)
    for node in (sl.best_effort(plain), sl.budget(sl.best_effort(sl.paragraph("Fits")), min=8, prefer=8)):
        result = plan(node, target=target, strict=True)
        assert "Fits" in scene.Codec.dumps(result.scene)
        assert not result.report.events


def test_best_effort_shortens_prose_and_zero_capacity_can_spill_all_fields() -> None:
    result = plan(sl.best_effort(sl.paragraph("abcdefghijk")), target=SMALL_TARGETS[0])
    assert "abcdefgh…" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Shortening"]
    empty = plan(
        sl.best_effort(sl.fields(sl.field("Name", "Value"))),
        target=components_v2_target(DISCORD, limits=V2Limits(total_text=0)),
    )
    assert not empty.scene.body.children
    assert [event.message for event in empty.report.events] == ["Omission"]


@pytest.mark.parametrize("wrapper", [sl.truncate, sl.best_effort])
def test_prose_permissions_cross_checked_presentation_graphs(wrapper: Any) -> None:
    source = sl.paragraph("abcdefghijk", key="body")
    document = wrapper(
        sl.alternatives(sl.alternative(source, key="same", fidelity=sl.equivalent()), content=source, key="choice")
    )
    result = plan(document, target=SMALL_TARGETS[0])
    assert "abcdefgh…" in scene.Codec.dumps(result.scene)
    assert [event.path.split("/")[-1] for event in result.report.events] == ["body"]


def test_spill_permission_crosses_checked_presentation_graphs() -> None:
    source = sl.fields(
        sl.field("H", "high", key="high", importance=Importance.HIGH), sl.field("L", "low value too long", key="low")
    )
    document = sl.spill(
        sl.alternatives(sl.alternative(source, key="same", fidelity=sl.equivalent()), content=source, key="choice")
    )
    result = plan(document, target=SMALL_TARGETS[0], chrome=CHROME)
    assert "high" in scene.Codec.dumps(result.scene)
    assert all("low" in event.path for event in result.report.events)


def test_equivalent_alternative_cannot_introduce_permission_to_relax_a_hard_floor() -> None:
    source = sl.budget(sl.paragraph("Fits", key="body"), min=8, prefer=8)
    choice = sl.alternative(
        sl.budget(sl.best_effort(sl.paragraph("Fits", key="body")), min=8, prefer=8),
        key="relaxed",
        fidelity=sl.equivalent(),
        correspondence=(sl.corresponds("body", "body"),),
    )
    with pytest.raises(LayoutInvariantError, match="preserve the Budgeted boundary"):
        check_alternative(sl.alternatives(choice, content=source, key="choice"), choice)


def test_best_effort_floor_permission_crosses_a_presentation_graph() -> None:
    source = sl.budget(sl.paragraph("Fits", key="body"), min=8, prefer=8)
    document = sl.best_effort(
        sl.alternatives(sl.alternative(source, key="same", fidelity=sl.equivalent()), content=source, key="choice")
    )
    result = plan(document, target=components_v2_target(DISCORD, limits=V2Limits(total_text=4)), strict=True)
    assert "Fits" in scene.Codec.dumps(result.scene)
    assert not result.report.events


def test_best_effort_cannot_drop_controls_to_meet_a_component_limit() -> None:
    async def click(event: sl.PressEvent) -> None:
        pytest.fail("planning invoked a callback")

    node = sl.best_effort(sl.action_controls(sl.action_control("Keep", click, key="keep"), key="actions"))
    with pytest.raises(JointInfeasibleError):
        plan(node, target=components_v2_target(DISCORD, limits=V2Limits(total_components=0)))


@pytest.mark.parametrize("count", [2, 5, 20])
def test_intermediate_shortening_probes_keep_useful_content_with_bounded_work(count: int) -> None:
    document = sl.best_effort(sl.stack(*(sl.paragraph("x" * 100, key=str(index)) for index in range(count))))
    result = plan(document, target=components_v2_target(DISCORD, limits=V2Limits(total_text=100)))
    assert len(result.scene.body.children) == count
    assert sum(len(node.content) for node in result.scene.body.children if isinstance(node, scene.Text)) == 100
    assert result.metrics.work_used <= max(50_000, count * 100 * 200)


@pytest.mark.parametrize("target", [components_v2_target(DISCORD), sl.html.target()], ids=lambda target: target.id)
def test_positive_stretch_is_a_finite_allowance(target: Target[Any, Any, Any, Any]) -> None:
    options = CompositionOptions(automatic=True)
    inside = plan(sl.budget(sl.paragraph("123456"), min=2, prefer=4, stretch=2), target=target, composition=options)
    assert inside.cost.get(Axis.DISPLAY_TEXT) == 6
    with pytest.raises(JointInfeasibleError):
        plan(sl.budget(sl.paragraph("1234567"), min=2, prefer=4, stretch=2), target=target, composition=options)
    shortened = plan(sl.best_effort(sl.budget(sl.paragraph("1234567"), min=2, prefer=4, stretch=2)), target=target)
    assert shortened.cost.get(Axis.DISPLAY_TEXT) == 6
    assert [event.message for event in shortened.report.events] == ["Shortening"]
