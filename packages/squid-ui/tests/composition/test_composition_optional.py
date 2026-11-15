"""Optional content loses only its own sources and respects authored importance."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.authoring.semantic import Importance
from squid_ui.ext.discord.limits import ClassicLimits, V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.target import Target
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "optional.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "optional.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]
SMALL_TARGETS = [
    components_v2_target(DISCORD, limits=V2Limits(total_components=1)),
    classic_target(DISCORD, limits=ClassicLimits(content=4)),
    *(replace(target, limits=replace(target.limits, blocks=1)) for target in TARGETS[3:]),
]


async def untouched(event: sl.PressEvent) -> None:
    pytest.fail("planning invoked optional content")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_optional_content_is_retained_when_it_fits(target: Target[Any, Any, Any, Any]) -> None:
    result = plan(sl.optional(sl.paragraph("Optional body", key="body")), target=target, strict=True)
    assert result.allocation is not None
    assert "Optional body" in scene.Codec.dumps(result.scene)
    assert not any(event.code == "composition.effect" for event in result.report.events)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_explicit_omission_reports_sources_and_removes_bindings(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.group(
        sl.paragraph("Required", key="required"),
        sl.optional(
            sl.stack(
                sl.paragraph("Optional body", key="body"),
                sl.action_controls(sl.action_control("Optional action", untouched, key="action"), key="actions"),
                key="extra",
            )
        ),
        key="root",
    )
    options = CompositionOptions(automatic=True, selections={OccurrenceId(("root", "root", "@1")): "omit"})
    result = plan(node, target=target, composition=options)
    assert "Required" in scene.Codec.dumps(result.scene)
    assert "Optional" not in scene.Codec.dumps(result.scene)
    assert not result.bindings
    effects = [event for event in result.report.events if event.code == "composition.effect"]
    assert len(effects) == 2
    assert all(event.message == "Omission" for event in effects)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=options, strict=True)


@pytest.mark.parametrize("target", SMALL_TARGETS, ids=lambda target: target.id)
def test_pressure_omits_only_optional_content(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.group(sl.paragraph("Keep", key="required"), sl.optional(sl.paragraph("Drop", key="extra")))
    result = plan(node, target=target)
    assert "Keep" in scene.Codec.dumps(result.scene)
    assert "Drop" not in scene.Codec.dumps(result.scene)
    assert any(event.code == "composition.effect" for event in result.report.events)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


@pytest.mark.parametrize("target", SMALL_TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("reverse", [False, True])
def test_importance_preserves_the_more_important_sibling(target: Target[Any, Any, Any, Any], reverse: bool) -> None:
    siblings = [
        sl.optional(sl.paragraph("High", key="high"), importance=Importance.HIGH),
        sl.optional(sl.paragraph("Low", key="low"), importance=Importance.LOW),
    ]
    result = plan(sl.group(*(reversed(siblings) if reverse else siblings)), target=target)
    assert "High" in scene.Codec.dumps(result.scene)
    assert "Low" not in scene.Codec.dumps(result.scene)


def test_lossless_pagination_precedes_omission() -> None:
    node = sl.region(
        sl.paragraph("Keep", key="required"),
        sl.paragraph("Also", key="also"),
        sl.optional(sl.paragraph("Extra", key="extra")),
        key="r",
        title="R",
    )
    result = plan(node, target=components_v2_target(DISCORD, limits=V2Limits(total_components=2)))
    assert result.allocation is not None
    region = OccurrenceId(("root", "r"))
    pages = [result.allocation.select({region: index}) for index in range(3)]
    assert "Keep" in scene.Codec.dumps(pages[0].scene)
    assert "Also" in scene.Codec.dumps(pages[1].scene)
    assert "Extra" in scene.Codec.dumps(pages[2].scene)
    assert all(not any(event.code == "composition.effect" for event in page.report.events) for page in pages)
