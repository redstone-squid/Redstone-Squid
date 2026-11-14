"""Explicit prose shortening preserves styling and never changes control behavior."""

from dataclasses import replace
from typing import Any, Literal

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.planning import OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.limits import ClassicLimits, V2Limits
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import Target
from squid_ui.presentations import budget
from squid_ui.primitives import nodes as native
from squid_ui.semantic import Importance
from squid_ui.target_types import DiscordAdapter, RenderTarget, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "truncate.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "truncate.slack", "1")
SLACK_TARGETS: list[Target[Any, Any, Any, Any]] = [
    sl.slack.message_target(adapter=SLACK),
    sl.slack.home_target(adapter=SLACK),
]
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD, limits=V2Limits(total_text=10)),
    classic_target(DISCORD, limits=ClassicLimits(content=10)),
    sl.html.target(),
    *(
        replace(target, limits=replace(target.limits, components=replace(target.limits.components, section_text=10)))
        for target in SLACK_TARGETS
    ),
]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("keep", ["head", "tail"])
def test_truncation_retains_the_requested_end_and_reports_loss(
    target: Target[Any, Any, Any, Any], keep: Literal["head", "tail"]
) -> None:
    node = sl.truncate(sl.paragraph("abcdefghijklmnopqrst", key="body"), keep=keep)
    constrained = budget(node, limits={Axis.DISPLAY_TEXT: 10}) if target.id == "html.semantic" else node
    result = plan(constrained, target=target)
    assert result.allocation is not None
    expected = "abcdefghi…" if keep == "head" else "…lmnopqrst"
    rendered = (
        sl.html.Renderer().draw(result.scene, plan=result)
        if isinstance(result.scene.body, scene.HtmlBody)
        else scene.Codec.dumps(result.scene)
    )
    assert expected in rendered
    assert any(event.code == "composition.effect" and event.message == "Shortening" for event in result.report.events)
    with pytest.raises(JointInfeasibleError):
        plan(constrained, target=target, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_styled_shortening_keeps_balanced_markup(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.truncate(sl.paragraph(sl.strong("abcdefghijklmnopqrst")))
    constrained = budget(node, limits={Axis.DISPLAY_TEXT: 10}) if target.id == "html.semantic" else node
    result = plan(constrained, target=target)
    wire = scene.Codec.dumps(result.scene)
    assert "abc" in wire and "abcdefghijklmnopqrst" not in wire
    if isinstance(result.scene.body, scene.HtmlBody):
        assert "<strong>abcdefghi</strong>…" in sl.html.Renderer().draw(result.scene, plan=result)
    else:
        assert "**abcde**…" in wire if target.id.startswith("discord") else "*abcdefg*…" in wire


def test_full_text_is_lossless_even_with_truncation_permission() -> None:
    result = plan(sl.truncate(sl.paragraph("Fits")), target=TARGETS[0], strict=True)
    assert "Fits" in scene.Codec.dumps(result.scene)
    assert not any(event.code == "composition.effect" for event in result.report.events)


def test_truncation_cannot_discard_or_shorten_a_control() -> None:
    async def click(event: sl.PressEvent) -> None:
        pytest.fail("planning invoked a callback")

    target = replace(
        TARGETS[0], limits=replace(TARGETS[0].limits, components=replace(TARGETS[0].limits.components, button_label=2))
    )
    node = sl.truncate(sl.action_controls(sl.action_control("Long label", click, key="action"), key="actions"))
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target)


def test_lossless_pagination_precedes_shortening() -> None:
    target = components_v2_target(DISCORD, limits=V2Limits(total_text=30))
    node = sl.region(sl.truncate(sl.paragraph("x" * 40, key="body")), key="r", title="R")
    result = plan(node, target=target)
    assert result.allocation is not None
    region = OccurrenceId(("root", "r"))
    text: list[str] = []
    for page_index in range(len(result.allocation.regions[region].cuts)):
        page = result.allocation.select({region: page_index})
        text.extend(
            node.content
            for node in page.scene.body.children
            if isinstance(node, scene.Text) and set(node.content) == {"x"}
        )
        assert not any(event.code == "composition.effect" for event in page.report.events)
    assert "".join(text) == "x" * 40


@pytest.mark.parametrize("reverse", [False, True])
def test_importance_preserves_the_higher_priority_prose(reverse: bool) -> None:
    nodes = [sl.paragraph("AAAAAA", importance=Importance.HIGH), sl.paragraph("BBBBBB", importance=Importance.LOW)]
    result = plan(
        sl.truncate(sl.stack(*(reversed(nodes) if reverse else nodes))),
        target=components_v2_target(DISCORD, limits=V2Limits(total_text=8)),
    )
    wire = scene.Codec.dumps(result.scene)
    assert "AAAAAA" in wire and "BBBBBB" not in wire


def test_native_truncate_policy_is_available_in_shared_regions() -> None:
    result = plan(sl.region(native.Text("abcdefghijklmnopqrst"), key="r", title="R"), target=TARGETS[0])
    assert result.allocation is not None
    assert "abcdefghi…" in scene.Codec.dumps(result.scene)


def test_heading_importance_precedes_body_and_note() -> None:
    result = plan(
        sl.truncate(sl.stack(sl.note("BBBBBB"), sl.paragraph("CCCCCC"), sl.heading("AAAAAA"))),
        target=components_v2_target(DISCORD, limits=V2Limits(total_text=12)),
    )
    wire = scene.Codec.dumps(result.scene)
    assert "AAAAAA" in wire and "BBBBBB" not in wire


def test_unsupported_rich_shortening_reports_its_capability() -> None:
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.truncate(sl.paragraph(sl.text_link("Long label", "https://example.com"))), target=TARGETS[0])
    assert "shortening inline link" in str(error.value.diagnostics.violations)


@pytest.mark.parametrize("node", [sl.paragraph("Remove"), sl.heading("Remove"), sl.note("Remove"), sl.code("Remove")])
def test_zero_text_grant_can_remove_all_prose_with_an_explicit_effect(node: sl.Renderable[RenderTarget]) -> None:
    result = plan(sl.truncate(node), target=components_v2_target(DISCORD, limits=V2Limits(total_text=0)))
    assert not result.scene.body.children
    assert any(event.message == "Shortening" for event in result.report.events)
