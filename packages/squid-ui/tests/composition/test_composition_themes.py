"""Palette scopes survive shared search, presentation alternatives, and retained turns."""

from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.ext.html import scene as html_scene
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.palette import Palette
from squid_ui.planning import CompositionOptions, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.presentation_state import PresentationState
from squid_ui.selectors import query, select

ADAPTER = AdapterProfile(DiscordAdapter, "themes", "1")
TARGETS = [components_v2_target(ADAPTER), classic_target(ADAPTER), squid_ui.ext.html.target()]


def colours(body: scene.Body) -> list[int | None]:
    if isinstance(body, discord_scene.ComponentsV2):
        return [node.accent for node in body.children if isinstance(node, discord_scene.Panel)]
    if isinstance(body, discord_scene.ClassicMessage):
        return [embed.colour for embed in body.embeds]
    assert isinstance(body, html_scene.HtmlBody)

    return [
        match.node.colour.value
        for match in query(body, select(html_scene.HtmlElement)).all()
        if match.node.colour is not None
    ]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("automatic", [False, True])
def test_palette_scopes_survive_alternatives_without_leaking(
    target: Target[Any, Any, Any, Any], automatic: bool
) -> None:
    source = sl.block(sl.paragraph("alternative", key="text"), key="box")
    alternate = sl.alternatives(sl.alternative(source, key="same", fidelity=sl.equivalent()), content=source, key="alt")
    node = sl.group(
        sl.themed(
            Palette(brand=0x112233),
            sl.block(sl.paragraph("outer")),
            sl.themed(Palette(brand=0x445566), alternate),
            sl.block(sl.paragraph("outer again")),
        ),
        sl.block(sl.paragraph("sibling")),
    )
    result = plan(
        node, target=target, palette=Palette(brand=0x778899), composition=CompositionOptions(automatic=automatic)
    )
    assert colours(result.scene.body) == [0x112233, 0x445566, 0x112233, 0x778899]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_palette_changes_invalidate_delivered_allocation(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()

    def content(colour: int) -> sl.DocumentLike:
        return sl.themed(Palette(brand=colour), sl.region(sl.block(sl.paragraph("body")), key="r", title="R"))

    first = plan(content(0x112233), target=target, session=session)
    session.apply_updates(first.session_updates)
    repeated = plan(content(0x112233), target=target, session=session)
    assert repeated.allocation is first.allocation
    changed = plan(content(0x445566), target=target, session=session)
    assert changed.allocation is not first.allocation
    assert colours(changed.scene.body) == [0x445566]
    assert session.retained_composition is not None
    assert session.retained_composition.result.allocation is first.allocation


@pytest.mark.parametrize("home", [False, True])
def test_slack_accepts_themed_portable_content(home: bool) -> None:
    adapter = AdapterProfile(SlackAdapter, "themes.slack", "1")
    target: Target[Any, Any, Any, Any] = (
        squid_ui.ext.slack.home_target(adapter=adapter) if home else squid_ui.ext.slack.message_target(adapter=adapter)
    )
    node = sl.themed(Palette(brand=0x112233), sl.region(sl.paragraph("body"), key="r", title="R"))
    result = plan(node, target=target)
    assert result.allocation is not None


@pytest.mark.parametrize(
    "target",
    [
        *TARGETS,
        squid_ui.ext.slack.message_target(adapter=AdapterProfile(SlackAdapter, "status", "1")),
        squid_ui.ext.slack.home_target(adapter=AdapterProfile(SlackAdapter, "status", "1")),
    ],
    ids=lambda target: target.id,
)
def test_status_and_progress_use_shared_pagination(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.status("Ready", tone=sl.Tone.SUCCESS), sl.progress(3, maximum=7, label="Work"), key="r", title="R"
    )
    result = plan(node, target=target)
    assert result.allocation is not None
    wire = scene.Codec.dumps(result.scene)
    assert "Ready" in wire
    assert "3/7" in wire
    assert "Work" in wire
    if isinstance(result.scene.body, html_scene.HtmlBody):
        status, progress = result.scene.body.children
        assert isinstance(status, html_scene.HtmlElement)
        assert html_scene.HtmlAttribute(html_scene.HtmlAttributeName.ROLE, "status") in status.attributes
        assert isinstance(progress, html_scene.HtmlElement)
        assert progress.tag == html_scene.HtmlTag.PROGRESS
        assert html_scene.HtmlAttribute(html_scene.HtmlAttributeName.VALUE, 3) in progress.attributes
        assert html_scene.HtmlAttribute(html_scene.HtmlAttributeName.MAX, 7) in progress.attributes


def test_html_status_resolves_its_colour_from_the_nearest_palette() -> None:
    palette = Palette(success=0x123456)
    result = plan(
        sl.themed(palette, sl.region(sl.status("Ready", tone=sl.Tone.SUCCESS), key="r", title="R")),
        target=squid_ui.ext.html.target(),
    )
    status = result.scene.body.children[0]
    assert isinstance(status, html_scene.HtmlElement)
    assert status.colour == html_scene.HtmlColourRef(0x123456)
