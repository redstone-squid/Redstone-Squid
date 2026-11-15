"""Lead images remain attached to repeated headings and consume page capacity."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.planning import CompositionOptions, JointInfeasibleError, JointUnsupportedError, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "thumbnails.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "thumbnails.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]
IMAGE = "https://example.invalid/lead.png"


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("article", [False, True])
def test_thumbnail_repeats_with_its_heading_on_every_page(target: Target[Any, Any, Any, Any], article: bool) -> None:
    shell = sl.article if article else sl.section
    node = sl.region(
        shell(
            sl.heading("Details"),
            sl.paragraph("first body", key="a"),
            sl.paragraph("second body", key="b"),
            thumbnail=IMAGE,
        ),
        key="r",
        title="R",
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (1, 2)}))
    assert result.allocation is not None
    for index, value in enumerate(("first body", "second body")):
        page = result.allocation.select({region: index}).scene
        wire = scene.Codec.dumps(page)
        assert IMAGE in wire and "Details" in wire and value in wire
        assert ("second body" if index == 0 else "first body") not in wire
        match page.body:
            case scene.ComponentsV2(children=children):
                panel = children[0]
                assert isinstance(panel, scene.Panel)
                heading = panel.children[0]
                assert isinstance(heading, scene.Section)
                assert heading.accessory == scene.Thumbnail(IMAGE)
                assert "Details" in heading.texts[0].content
            case scene.ClassicMessage(embeds=embeds):
                assert len(embeds) == 1
                assert embeds[0].thumbnail == scene.EmbedMedia(IMAGE)
            case scene.HtmlBody(children=children):
                element = children[0]
                assert isinstance(element, scene.HtmlElement)
                assert element.tag == (scene.HtmlTag.ARTICLE if article else scene.HtmlTag.SECTION)
                thumbnail = element.children[1]
                assert isinstance(thumbnail, scene.HtmlElement)
                assert thumbnail.tag == scene.HtmlTag.IMG
                assert thumbnail.url == scene.HtmlUrlRef(IMAGE)
                assert scene.HtmlAttribute(scene.HtmlAttributeName.ALT, "Details") in thumbnail.attributes
            case scene.SlackMessage(blocks=blocks) | scene.SlackHomeView(blocks=blocks):
                assert isinstance(blocks[0], scene.SlackHeader)
                assert blocks[1] == scene.SlackImage(IMAGE, "Details")
            case _:
                pytest.fail("unexpected document target")


@pytest.mark.parametrize("target", [TARGETS[0], *TARGETS[3:]], ids=lambda target: target.id)
def test_thumbnail_cost_respects_reserved_capacity(target: Target[Any, Any, Any, Any]) -> None:
    section = sl.section(sl.heading("Details"), sl.paragraph("body"))
    node = sl.region(section, key="r", title="R")
    with_image = replace(node, children=(replace(section, thumbnail=IMAGE),))
    before = plan(node, target=target)
    after = plan(with_image, target=target)
    axis = Axis.COMPONENTS if isinstance(after.scene.body, scene.ComponentsV2) else Axis.BLOCKS
    extra = 2 if axis == Axis.COMPONENTS else 1
    assert after.cost.get(axis) == before.cost.get(axis) + extra
    capacity = target.limits.total_components if axis == Axis.COMPONENTS else target.limits.blocks
    reservation = ResourceCost({axis: capacity - before.cost.get(axis)})
    plan(node, target=target, reservation=reservation)
    with pytest.raises(JointInfeasibleError):
        plan(with_image, target=target, reservation=reservation)


def test_nested_v2_section_keeps_its_thumbnail_inside_the_enclosing_panel() -> None:
    node = sl.region(
        sl.block(sl.section(sl.heading("Details"), sl.paragraph("body"), thumbnail=IMAGE)), key="r", title="R"
    )
    result = plan(node, target=TARGETS[0])
    panel = result.scene.body.children[0]
    assert isinstance(panel, scene.Panel)
    assert isinstance(panel.children[0], scene.Section)
    assert panel.children[0].accessory == scene.Thumbnail(IMAGE)
    assert result.cost.get(Axis.COMPONENTS) == 5


def test_classic_rejects_a_thumbnail_that_cannot_belong_to_its_own_embed() -> None:
    node = sl.region(
        sl.block(sl.section(sl.heading("Details"), sl.paragraph("body"), thumbnail=IMAGE)), key="r", title="R"
    )
    with pytest.raises(JointUnsupportedError) as caught:
        plan(node, target=TARGETS[1])
    assert "thumbnail inside an enclosing classic embed" in str(caught.value.diagnostics.violations)


@pytest.mark.parametrize("target", TARGETS[3:], ids=lambda target: target.id)
def test_slack_preserves_section_content_without_an_accent(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(sl.section(sl.heading("Details"), thumbnail=IMAGE, accent=0x123456), key="r", title="R")
    result = plan(node, target=target)
    assert "Details" in scene.Codec.dumps(result.scene)
    assert IMAGE in scene.Codec.dumps(result.scene)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_thumbnail_changes_invalidate_a_delivered_allocation(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()

    def content(image: str) -> sl.DocumentLike:
        return sl.region(sl.section(sl.heading("Details"), sl.paragraph("body"), thumbnail=image), key="r", title="R")

    first = plan(content(IMAGE), target=target, session=session)
    session.apply_updates(first.session_updates)
    repeated = plan(content(IMAGE), target=target, session=session)
    assert repeated.allocation is first.allocation
    changed = plan(content("https://example.invalid/new.png"), target=target, session=session)
    assert changed.allocation is not first.allocation
    assert IMAGE not in scene.Codec.dumps(changed.scene)
