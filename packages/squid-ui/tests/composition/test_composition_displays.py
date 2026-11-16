"""Portable displays retain target metadata inside shared page families."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_i18n import Localization, tr
from squid_ui import scene
from squid_ui.authoring.semantic import TimeStyle
from squid_ui.content.temporal import ZonedDateTime
from squid_ui.ext.discord.limits import ClassicLimits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.slack.protocol import SlackHomeLimits, SlackMessageLimits
from squid_ui.palette import Palette
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.primitives import nodes as native
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "displays", "1")
SLACK = AdapterProfile(SlackAdapter, "displays", "1")
TARGETS = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]
INSTANT = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
ZONED = ZonedDateTime(INSTANT, "Europe/Brussels")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_displays_have_shared_pages_and_exact_target_metadata(target: Target[Any, Any, Any, Any]) -> None:
    identity = OccurrenceId(("root", "r"))
    result = plan(
        sl.region(
            sl.metric(12.5, "Delay", unit="ticks"),
            sl.timestamp(INSTANT, style=TimeStyle.RELATIVE, label="Updated"),
            sl.zoned_timestamp(ZONED, label="Local"),
            key="r",
            title="R",
        ),
        target=target,
        strict=True,
        composition=CompositionOptions(automatic=True, partitions={identity: (1, 2, 3)}),
    )
    assert result.allocation is not None
    pages = [result.allocation.select({identity: index}) for index in range(3)]
    assert all(page.cost.values for page in pages)
    assert "12.5 ticks" in scene.Codec.dumps(pages[0].scene)
    if isinstance(pages[0].scene.body, scene.HtmlBody):
        metric = pages[0].scene.body.children[0]
        assert isinstance(metric, scene.HtmlElement) and metric.tag == scene.HtmlTag.DL
        for page, style, zone in ((pages[1], "R", None), (pages[2], None, ZONED.timezone)):
            paragraph = page.scene.body.children[0]
            assert isinstance(paragraph, scene.HtmlElement)
            time = paragraph.children[-1]
            assert isinstance(time, scene.HtmlElement)
            assert time.time == scene.HtmlTimeRef(INSTANT.isoformat(), timezone=zone, style=style)
    elif isinstance(pages[1].scene.body, scene.ComponentsV2):
        assert pages[1].scene.body.children[0] == scene.Time(INSTANT.isoformat(), "R", "**Updated:** ")
        assert pages[2].scene.body.children[0] == scene.ZonedTime(INSTANT.isoformat(), ZONED.timezone, "**Local:** ")
    else:
        assert ZONED.isoformat() in scene.Codec.dumps(pages[2].scene)
        token = (
            f"<t:{int(INSTANT.timestamp())}:R>"
            if target.id.startswith("discord")
            else f"<!date^{int(INSTANT.timestamp())}^{{ago}}|"
        )
        assert token in scene.Codec.dumps(pages[1].scene)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_display_text_cannot_escape_a_hard_resource_cap(target: Target[Any, Any, Any, Any]) -> None:
    if isinstance(target.limits, ClassicLimits):
        target = replace(target, limits=replace(target.limits, content=1))
    elif isinstance(target.limits, SlackMessageLimits | SlackHomeLimits):
        target = replace(
            target, limits=replace(target.limits, components=replace(target.limits.components, section_text=1))
        )
    for content in (sl.metric(100, "Count"), sl.timestamp(INSTANT), sl.zoned_timestamp(ZONED)):
        constrained = (
            sl.presentations.budget(content, limits={Axis.DISPLAY_TEXT: 1})
            if target.id in ("html.semantic", "discord.components-v2")
            else content
        )
        with pytest.raises(JointInfeasibleError):
            plan(sl.region(constrained, key="r", title="R"), target=target)


@pytest.mark.parametrize("target", TARGETS[:3], ids=lambda target: target.id)
def test_aside_continuations_preserve_palette_and_reuse(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    identity = OccurrenceId(("root", "r"))

    def document(tone: sl.Tone) -> sl.DocumentLike:
        return sl.region(sl.aside(sl.paragraph("A"), sl.paragraph("B"), tone=tone), key="r", title="R")

    options = CompositionOptions(automatic=True, partitions={identity: (1, 2)})
    palette = Palette(warning=0x123456, success=0x654321)
    first = plan(document(sl.Tone.WARNING), target=target, session=session, palette=palette, composition=options)
    assert first.allocation is not None
    for page in (first, first.allocation.select({identity: 1})):
        body = page.scene.body
        if isinstance(body, scene.ComponentsV2):
            assert isinstance(body.children[0], scene.Panel) and body.children[0].accent == 0x123456
        elif isinstance(body, scene.ClassicMessage):
            assert body.embeds[0].colour == 0x123456
        else:
            assert isinstance(body, scene.HtmlBody)
            aside = body.children[0]
            assert isinstance(aside, scene.HtmlElement) and aside.tag == scene.HtmlTag.ASIDE
            assert aside.colour == scene.HtmlColourRef(0x123456)
            assert scene.HtmlAttribute(scene.HtmlAttributeName.TONE, "warning") in aside.attributes
    session.apply_updates(first.session_updates)
    assert (
        plan(document(sl.Tone.WARNING), target=target, session=session, palette=palette, composition=options).allocation
        is first.allocation
    )
    changed = plan(document(sl.Tone.SUCCESS), target=target, session=session, palette=palette, composition=options)
    assert changed.allocation is not first.allocation


@pytest.mark.parametrize("target", TARGETS[3:], ids=lambda target: target.id)
def test_slack_preserves_aside_content_without_an_accent(target: Target[Any, Any, Any, Any]) -> None:
    result = plan(sl.region(sl.aside("Warning", tone=sl.Tone.WARNING), key="r", title="R"), target=target)
    assert "Warning" in scene.Codec.dumps(result.scene)


@pytest.mark.parametrize("target", TARGETS[:2], ids=lambda target: target.id)
def test_native_time_prefixes_remain_authored_markdown(target: Target[Any, Any, Any, Any]) -> None:
    result = plan(
        sl.region(native.Time(INSTANT, "F", "**Starts:** "), native.ZonedTime(ZONED, "Local: "), key="r", title="R"),
        target=target,
    )
    assert result.allocation is not None
    assert "**Starts:** " in scene.Codec.dumps(result.scene)
    if isinstance(result.scene.body, scene.ComponentsV2):
        assert result.scene.body.children[0] == scene.Time(INSTANT.isoformat(), "F", "**Starts:** ")
        assert result.scene.body.children[1] == scene.ZonedTime(INSTANT.isoformat(), ZONED.timezone, "Local: ")
        assert result.cost.get(Axis.DISPLAY_TEXT) == len(f"**Starts:** <t:{int(INSTANT.timestamp())}:F>") + len(
            "Local: " + ZONED.isoformat()
        )


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("content", ["", " \n\t "])
def test_empty_notes_have_no_output_or_layout_cost(target: Target[Any, Any, Any, Any], content: str) -> None:
    baseline = plan(sl.stack(sl.paragraph("Visible")), target=target, strict=True)
    result = plan(sl.stack(sl.note(content), sl.paragraph("Visible")), target=target, strict=True)

    assert result.scene == baseline.scene
    assert result.allocation is not None
    assert baseline.allocation is not None
    assert result.allocation.select({}).cost == baseline.allocation.select({}).cost
    assert not result.report.events


@pytest.mark.parametrize("target", TARGETS[:2], ids=lambda target: target.id)
@pytest.mark.parametrize("content", ["", " \n\t "])
def test_empty_native_footers_have_no_output(target: Target[Any, Any, Any, Any], content: str) -> None:
    baseline = plan(sl.stack(sl.paragraph("Visible")), target=target, strict=True)
    result = plan(sl.stack(native.Footer(content), sl.paragraph("Visible")), target=target, strict=True)

    assert result.scene == baseline.scene
    assert not result.report.events


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_note_visibility_uses_localized_content(target: Target[Any, Any, Any, Any]) -> None:
    note = sl.note(tr(t"test-optional-note = Optional detail"))
    localization = Localization("en", ('test-optional-note = { "" }',))
    baseline = plan(sl.stack(sl.paragraph("Visible")), target=target, strict=True, localization=localization)
    empty = plan(
        sl.stack(note, sl.paragraph("Visible")),
        target=target,
        strict=True,
        localization=localization,
    )
    visible = plan(sl.stack(note, sl.paragraph("Visible")), target=target, strict=True)

    assert empty.scene == baseline.scene
    assert "Optional detail" in scene.Codec.dumps(visible.scene)
