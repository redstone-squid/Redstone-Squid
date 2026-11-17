"""Legacy replacement permission is explicit loss unless equivalence is declared."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.authoring.presentations import alternative, alternatives, budget, corresponds, equivalent
from squid_ui.ext.discord.limits import ClassicLimits, V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.ext.slack.target_types import SlackAdapter
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.resources import Axis
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState

DISCORD = AdapterProfile(DiscordAdapter, "fallback.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "fallback.slack", "1")
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


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_primary_is_lossless_but_forced_replacement_is_not(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.fallback(sl.paragraph("Complete", key="source"), sl.paragraph("Short", key="summary"))
    full = plan(node, target=target, strict=True)
    assert full.allocation is not None
    assert "Complete" in scene.Codec.dumps(full.scene)
    assert not any(event.code == "composition.effect" for event in full.report.events)
    options = CompositionOptions(automatic=True, selections={OccurrenceId(("root", "@0")): "fallback-1"})
    short = plan(node, target=target, composition=options)
    assert "Short" in scene.Codec.dumps(short.scene)
    assert "Complete" not in scene.Codec.dumps(short.scene)
    assert any(event.message == "Replacement" for event in short.report.events)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=options, strict=True)


@pytest.mark.parametrize("target", SMALL_TARGETS, ids=lambda target: target.id)
def test_pressure_chooses_the_first_fitting_authored_replacement(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.fallback(sl.stack(sl.paragraph("Long"), sl.paragraph("Body")), sl.paragraph("Tiny"), sl.paragraph("X"))
    result = plan(node, target=target)
    assert "Tiny" in scene.Codec.dumps(result.scene)
    assert any(event.message == "Replacement" for event in result.report.events)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_unselected_fallback_never_publishes_callbacks(target: Target[Any, Any, Any, Any]) -> None:
    async def primary(event: sl.PressEvent) -> None:
        pass

    async def replacement(event: sl.PressEvent) -> None:
        pass

    node = sl.fallback(
        sl.action_controls(sl.action_control("Primary", primary, key="action"), key="actions"),
        sl.action_controls(sl.action_control("Alternate", replacement, key="action"), key="actions"),
    )
    options = CompositionOptions(automatic=True, selections={OccurrenceId(("root", "@0")): "fallback-1"})
    session = PresentationState()
    full = plan(node, target=target, session=session)
    session.apply_updates(full.session_updates)
    changed = plan(node, target=target, session=session, composition=options)
    assert len(full.bindings) == len(changed.bindings) == 1
    assert next(iter(full.bindings.values())).handler is primary
    assert next(iter(changed.bindings.values())).handler is replacement
    assert full.allocation is not changed.allocation


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_fields_keep_labels_and_report_replacement_only_for_the_value(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.fields(sl.field("Label", "Complete", key="entry", fallbacks=("Short",)))
    options = CompositionOptions(automatic=True, selections={OccurrenceId(("root", "@0", "entry")): "field-fallback-1"})
    result = plan(node, target=target, composition=options)
    assert "Label" in scene.Codec.dumps(result.scene) and "Short" in scene.Codec.dumps(result.scene)
    effects = [event for event in result.report.events if event.code == "composition.effect"]
    assert len(effects) == 1 and effects[0].path.endswith("entry/value")
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=options, strict=True)


def test_equivalence_requires_checked_alternatives() -> None:
    source = sl.paragraph("Four", key="source")
    compact = sl.paragraph("4", key="compact")
    node = alternatives(
        alternative(source, key="full", fidelity=equivalent()),
        alternative(compact, key="short", fidelity=equivalent(), correspondence=(corresponds("compact", "source"),)),
        content=source,
        key="equivalent",
    )
    result = plan(
        budget(node, limits={Axis.DISPLAY_TEXT: 1}),
        target=TARGETS[0],
        strict=True,
        composition=CompositionOptions(automatic=True),
    )
    assert "4" in scene.Codec.dumps(result.scene)
    assert not any(event.code == "composition.effect" for event in result.report.events)


def test_many_field_ladders_find_a_certificate_within_the_work_budget() -> None:
    node = sl.fields(
        *(sl.field(f"Field {index}", "x" * 400, key=str(index), fallbacks=("short",)) for index in range(20))
    )
    result = plan(node, target=TARGETS[0])
    assert result.report.search is not None
    assert result.metrics.work_used <= result.report.search.work_limit
    wire = scene.Codec.dumps(result.scene)
    assert all(f"Field {index}:" in wire for index in range(20))
    assert result.cost.get(Axis.DISPLAY_TEXT) <= 4000
    assert result.allocation is not None


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_checked_container_does_not_erase_nested_replacement_effects(target: Target[Any, Any, Any, Any]) -> None:
    inner = sl.fallback(sl.paragraph("Full", key="source"), sl.paragraph("Short", key="compact"))
    node = alternatives(alternative(inner, key="wrapped", fidelity=equivalent()), content=inner, key="outer")
    options = CompositionOptions(
        automatic=True, selections={OccurrenceId(("root", "outer", "@presentation")): "fallback-1"}
    )
    result = plan(node, target=target, composition=options)
    effects = [event for event in result.report.events if event.code == "composition.effect"]
    assert effects and all(event.path.startswith("@document/outer/") for event in effects)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=options, strict=True)


def test_classic_fields_preserve_embed_slots_across_continuation_pages() -> None:
    limits = ClassicLimits()
    target = classic_target(DISCORD, limits=replace(limits, embeds=replace(limits.embeds, fields=2)))
    node = sl.region(
        sl.section(
            sl.heading("Fields"), sl.fields(*(sl.field(str(index), "value", key=str(index)) for index in range(5)))
        ),
        key="r",
        title="R",
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (2, 4, 5)}))
    assert result.allocation is not None
    names: list[str] = []
    for page_index in range(3):
        page = result.allocation.select({region: page_index})
        assert page.scene.body.embeds[0].title == "Fields"
        assert all(len(embed.fields) <= 2 for embed in page.scene.body.embeds)
        names.extend(field.name for embed in page.scene.body.embeds for field in embed.fields)
    assert names == [str(index) for index in range(5)]


def test_classic_field_replacements_obey_native_value_limits() -> None:
    limits = ClassicLimits()
    target = classic_target(DISCORD, limits=replace(limits, embeds=replace(limits.embeds, field_value=2)))
    node = sl.fields(sl.field("Name", "Too long", key="name", fallbacks=("OK",)))
    result = plan(node, target=target)
    assert result.scene.body.embeds[0].fields[0].value == "OK"
    assert result.cost.get(Axis.EMBED_TEXT) == 6
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


def test_classic_field_text_counts_against_the_authored_embed_budget() -> None:
    fields = sl.fields(sl.field("A", "Long", key="a"))
    result = plan(sl.region(budget(fields, limits={Axis.EMBED_TEXT: 5}), key="r", title="R"), target=TARGETS[1])
    assert result.cost.get(Axis.EMBED_TEXT) == 5
    assert not result.scene.body.content
    with pytest.raises(JointInfeasibleError):
        plan(sl.region(budget(fields, limits={Axis.EMBED_TEXT: 4}), key="r", title="R"), target=TARGETS[1])
