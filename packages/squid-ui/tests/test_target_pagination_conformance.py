"""Every document surface participates in the same allocation and delivery contract."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.planning import CompositionOptions, JointUnsupportedError, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.limits import ClassicLimits
from squid_ui.planning.navigation import default_nav
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "conformance.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "conformance.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    sl.html.target(),
    sl.slack.message_target(adapter=SLACK),
    sl.slack.home_target(adapter=SLACK),
]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_independent_pages_preserve_content_and_reuse_delivered_allocation(target: Target[Any, Any, Any, Any]) -> None:
    document = sl.group(
        *(
            sl.region(*(sl.paragraph(f"{key}{i}", key=str(i)) for i in range(2)), key=key, title=key)
            for key in ("left", "right")
        ),
        key="body",
    )
    left = OccurrenceId(("root", "body", "left"))
    right = OccurrenceId(("root", "body", "right"))
    options = CompositionOptions(automatic=True, partitions={left: (1, 2), right: (1, 2)})
    session = PresentationState()
    first = plan(document, target=target, composition=options, session=session, nav=default_nav)
    assert first.allocation is not None
    for a in range(2):
        for b in range(2):
            page = first.allocation.select({left: a, right: b})
            wire = scene.Codec.dumps(page.scene)
            assert f"left{a}" in wire and f"right{b}" in wire
            assert f"left{1 - a}" not in wire and f"right{1 - b}" not in wire
    assert session.retained_composition is None
    session.apply_updates(first.session_updates)
    repeated = plan(document, target=target, composition=options, session=session, nav=default_nav)
    assert repeated.allocation is first.allocation
    assert repeated.metrics.work_used == 0


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_production_list_uses_shared_search(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.semantic.List(
        tuple(sl.semantic.ListItem(str(i), f"item {i}") for i in range(3)), key="items", page_size=1
    )
    result = plan(node, target=target, nav=default_nav)
    assert result.allocation is not None
    assert result.report.legacy_pagination is None
    assert result.scene.pagers[0].pages == 3


def test_classic_content_and_embed_pools_are_independent() -> None:
    target = replace(classic_target(DISCORD), limits=ClassicLimits(content=8, embed_text=20))
    result = plan(
        sl.group(sl.paragraph("outside"), sl.block(sl.paragraph("inside"))),
        target=target,
        composition=CompositionOptions(automatic=True),
    )
    body = result.scene.body
    assert isinstance(body, scene.ClassicMessage)
    assert body.content == "outside"
    assert body.embeds[0].description == "inside"


async def untouched(event: sl.PressEvent) -> None:
    pytest.fail("planning invoked a control")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_semantic_text_and_action_rows_share_the_production_route(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.heading("Heading"),
        sl.note("Note"),
        sl.code("print(1)"),
        sl.quote("Quote"),
        sl.fields(sl.field("Label", "Value")),
        sl.action_controls(*(sl.action_control(f"Action {i}", untouched, key=str(i)) for i in range(7)), key="actions"),
        key="content",
        title="Content",
    )
    result = plan(node, target=target, nav=default_nav)
    assert result.report.legacy_pagination is None
    assert result.allocation is not None
    wire = scene.Codec.dumps(result.scene)
    for value in ("Heading", "Note", "print(1)", "Quote", "Label", "Value"):
        assert value in wire
    assert len(result.bindings) == 7
    if isinstance(result.scene.body, scene.ClassicMessage):
        assert [len(row.controls) for row in result.scene.body.rows] == [5, 2]
    if isinstance(result.scene.body, scene.ComponentsV2):
        assert [len(row.items) for row in result.scene.body.children if isinstance(row, scene.Row)] == [5, 2]


@pytest.mark.parametrize("target", TARGETS[:3], ids=lambda target: target.id)
def test_section_heading_repeats_when_its_body_fragments(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.section(
            sl.heading("Details"),
            sl.paragraph("one", key="one"),
            sl.paragraph("two", key="two"),
            key="section",
            accent=None,
        ),
        key="r",
        title="R",
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (1, 2)}))
    assert result.allocation is not None
    for index, value in enumerate(("one", "two")):
        wire = scene.Codec.dumps(result.allocation.select({region: index}).scene)
        assert "Details" in wire and value in wire


@pytest.mark.parametrize("target", [TARGETS[0], TARGETS[1], TARGETS[2]], ids=lambda target: target.id)
def test_uncontrolled_toggle_changes_invalidate_retained_output(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.region(sl.action_controls(sl.toggle("Flag", key="flag"), key="actions"), key="r", title="R")
    first = plan(node, target=target, session=session, nav=default_nav)
    assert not session.toggles
    session.apply_updates(first.session_updates)
    session.set_toggle("flag", on=True)
    second = plan(node, target=target, session=session, nav=default_nav)
    assert second.allocation is not first.allocation
    assert first.scene != second.scene


def test_modal_regions_explain_the_form_surface_constraint() -> None:
    with pytest.raises(JointUnsupportedError) as caught:
        plan(sl.region(sl.paragraph("body"), key="r", title="R"), target=sl.slack.modal_target(adapter=SLACK))
    assert "one top-level form trigger" in str(caught.value.diagnostics.violations)


def test_unmapped_fallback_in_a_region_requires_explicit_correspondence() -> None:
    with pytest.raises(JointUnsupportedError) as caught:
        plan(sl.region(sl.fallback(sl.paragraph("full"), sl.paragraph("short")), key="r", title="R"), target=TARGETS[0])
    assert "FallbackContent" in str(caught.value.diagnostics.violations)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_images_and_choices_remain_bound_to_their_visible_page(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.figure("https://example.invalid/image.png", caption="Image caption"),
        sl.choices(sl.choice("First", key="first"), sl.choice("Second", key="second"), key="choose"),
        key="r",
        title="R",
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (1, 2)}))
    assert result.allocation is not None
    first = result.allocation.select({region: 0})
    second = result.allocation.select({region: 1})
    assert "image.png" in scene.Codec.dumps(first.scene)
    assert "Image caption" in scene.Codec.dumps(first.scene)
    assert not first.bindings
    assert "image.png" not in scene.Codec.dumps(second.scene)
    assert "choose" in second.bindings
