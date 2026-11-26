"""Every document surface participates in the same allocation and delivery contract."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.ext.discord import primitives
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.limits import ClassicLimits
from squid_ui.ext.discord.navigation_controls import default_nav
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.ext.slack import scene as slack_scene
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.presentation_state import PresentationState


async def _ignore(_event: object) -> None: ...


DISCORD = AdapterProfile(DiscordAdapter, "conformance.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "conformance.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
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
    assert result.scene.pagers[0].pages == 3


def test_classic_content_and_embed_pools_are_independent() -> None:
    target = replace(classic_target(DISCORD), limits=ClassicLimits(content=8, embed_text=20))
    result = plan(
        sl.group(sl.paragraph("outside"), sl.block(sl.paragraph("inside"))),
        target=target,
        composition=CompositionOptions(automatic=True),
    )
    body = result.scene.body
    assert isinstance(body, discord_scene.ClassicMessage)
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
    assert result.allocation is not None
    wire = scene.Codec.dumps(result.scene)
    for value in ("Heading", "Note", "print(1)", "Quote", "Label", "Value"):
        assert value in wire
    assert len(result.bindings) == 7
    if isinstance(result.scene.body, discord_scene.ClassicMessage):
        assert [len(row.controls) for row in result.scene.body.rows] == [5, 2]
    if isinstance(result.scene.body, discord_scene.ComponentsV2):
        assert [len(row.items) for row in result.scene.body.children if isinstance(row, discord_scene.Row)] == [5, 2]


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


def test_modal_regions_explain_the_form_surface_constraint() -> None:
    with pytest.raises(sl.LayoutInvariantError, match="one top-level"):
        plan(sl.region(sl.paragraph("body"), key="r", title="R"), target=squid_ui.ext.slack.modal_target(adapter=SLACK))


def test_legacy_fallback_in_a_region_retains_the_primary_without_loss() -> None:
    result = plan(
        sl.region(sl.fallback(sl.paragraph("full"), sl.paragraph("short")), key="r", title="R"),
        target=TARGETS[0],
        strict=True,
    )
    assert result.allocation is not None
    assert "full" in scene.Codec.dumps(result.scene)
    assert not any(event.code == "composition.effect" for event in result.report.events)


@pytest.mark.parametrize("target", [TARGETS[0], TARGETS[1], TARGETS[2]], ids=lambda target: target.id)
def test_node_value_changes_invalidate_retained_output(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.region(sl.details(sl.summary("More"), "Hidden body", key="more"), key="r", title="R")
    first = plan(node, target=target, session=session, nav=default_nav)
    session.apply_updates(first.session_updates)
    session.set_node_value("more", value=True)
    second = plan(node, target=target, session=session, nav=default_nav)
    assert second.allocation is not first.allocation
    assert first.scene != second.scene


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_images_and_choices_remain_bound_to_their_visible_page(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.figure("https://example.invalid/image.png", caption="Image caption"),
        sl.choices(sl.choice("First", key="first"), sl.choice("Second", key="second"), key="choose", on_change=_ignore),
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


def test_slack_list_continuations_do_not_insert_extra_separators() -> None:
    target = squid_ui.ext.slack.message_target(adapter=SLACK)
    target = replace(
        target, limits=replace(target.limits, components=replace(target.limits.components, section_text=120))
    )
    node = sl.semantic.List((sl.semantic.ListItem("item", "x" * 200),), key="items")
    identity = OccurrenceId(("root", "items"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={identity: (7, 14)}))
    assert result.allocation is not None
    content: list[str] = []
    for index in range(2):
        body = result.allocation.select({identity: index}).scene.body
        assert isinstance(body, slack_scene.SlackMessage)
        block = body.blocks[0]
        assert isinstance(block, slack_scene.SlackSection)
        assert block.text is not None
        content.append(block.text.content)
    assert "".join(content) == "• " + "x" * 200


@pytest.mark.parametrize("target", TARGETS[:3], ids=lambda target: target.id)
def test_repeated_media_deduplicates_its_attachment(target: Target[Any, Any, Any, Any]) -> None:
    asset = sl.document.Asset("image", "image.png", "image/png", sl.document.InlineAsset(b"image"))
    node = sl.region(
        sl.semantic.Media((sl.semantic.MediaItem("one", asset), sl.semantic.MediaItem("two", asset)), key="gallery"),
        key="r",
        title="R",
    )
    result = plan(node, target=target)
    assert result.allocation is not None
    assert tuple(result.resources) == ("asset:image",)
    assert len(result.scene.assets) == 1


def test_classic_count_based_primitive_pages_use_its_own_text_pool() -> None:
    node = primitives.Lines(("first", "second"), overflow=primitives.Paginate(key="lines", per=1))
    result = plan(node, target=classic_target(DISCORD), nav=default_nav)
    assert result.allocation is not None
    assert result.scene.pagers[0].pages == 2
    assert isinstance(result.scene.body, discord_scene.ClassicMessage)
    assert "first" in (result.scene.body.content or "")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_nested_control_groups_preserve_action_order(target: Target[Any, Any, Any, Any]) -> None:
    actions = tuple(sl.action_control(f"action-{index}", untouched, key=f"key-{index}") for index in range(3))
    node = sl.region(
        sl.action_controls(actions[0], sl.semantic.ControlGroup("nested", (actions[1],)), actions[2], key="actions"),
        key="r",
        title="R",
    )
    result = plan(node, target=target)
    assert result.allocation is not None
    wire = scene.Codec.dumps(result.scene)
    assert wire.index("action-0") < wire.index("action-1") < wire.index("action-2")
