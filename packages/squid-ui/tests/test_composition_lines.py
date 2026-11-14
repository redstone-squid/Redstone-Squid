"""Static line collections preserve complete entries unless their policy allows loss."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.planning import CompositionOptions, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.limits import ClassicLimits, V2Limits
from squid_ui.planning.target import Target
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Alt, Drop, Never, Truncate
from squid_ui.target_types import DiscordAdapter

ADAPTER = AdapterProfile(DiscordAdapter, "native-lines.discord", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(ADAPTER, limits=V2Limits(total_text=10)),
    classic_target(ADAPTER, limits=ClassicLimits(content=10)),
]
AUTOMATIC = CompositionOptions(automatic=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_spill_reports_whole_omitted_entries_and_reserves_its_notice(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines(("first", "second", "third"), keys=("one", "two", "three"))
    result = plan(node, target=target, composition=AUTOMATIC, chrome=Chrome(and_n_more=lambda count: f"+{count}"))
    assert "first\\n+2" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Omission", "Omission"]
    assert {event.path.split("/")[-1] for event in result.report.events} == {"two", "three"}
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, composition=AUTOMATIC, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_lines_never_cannot_silently_shorten_entries(target: Target[Any, Any, Any, Any]) -> None:
    with pytest.raises(JointInfeasibleError):
        plan(native.Lines(("first", "second"), overflow=Never()), target=target, composition=AUTOMATIC)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_line_truncation_keeps_joiner_and_requested_tail(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Lines(("abcdefgh", "ijklmnop"), join="/", overflow=Truncate("tail"), keys=("first", "last"))
    result = plan(node, target=target, composition=AUTOMATIC)
    assert "…/ijklmnop" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Shortening"]
    assert result.report.events[0].path.endswith("/first")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_line_drop_is_whole_collection_omission(target: Target[Any, Any, Any, Any]) -> None:
    result = plan(native.Lines(("first", "second"), overflow=Drop()), target=target, composition=AUTOMATIC)
    wire = scene.Codec.dumps(result.scene)
    assert "first" not in wire and "second" not in wire
    assert [event.message for event in result.report.events] == ["Omission", "Omission"]


def test_region_pagination_precedes_static_spill_and_preserves_entry_order() -> None:
    entries = tuple(f"entry-{index}" for index in range(6))
    result = plan(
        sl.region(native.Lines(entries), key="r", title="R"),
        target=components_v2_target(ADAPTER, limits=V2Limits(total_text=35)),
    )
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    reconstructed: list[str] = []
    for index in range(len(result.allocation.regions[identity].cuts)):
        page = result.for_page({identity: index})
        assert not page.report.events
        reconstructed.extend(
            line
            for node in page.scene.body.children
            if isinstance(node, scene.Text)
            for line in node.content.splitlines()
            if line.startswith("entry-")
        )
    assert tuple(reconstructed) == entries


def test_line_alternates_still_identify_the_missing_contract() -> None:
    with pytest.raises(JointUnsupportedError) as error:
        plan(sl.region(native.Lines((Alt("Long entry", ("Short",)),)), key="r", title="R"), target=TARGETS[0])
    assert "line degradation alternatives" in str(error.value.diagnostics)


def test_shortened_lines_in_checked_presentations_report_original_sources() -> None:
    source = native.Lines(("abcdefgh", "ijklmnop"), join="/", overflow=Never(), keys=("first", "last"))
    document = sl.alternatives(
        sl.alternative(replace(source, overflow=Truncate("tail")), key="shortenable", fidelity=sl.equivalent()),
        content=source,
        key="choice",
    )
    result = plan(document, target=TARGETS[0])
    assert "…/ijklmnop" in scene.Codec.dumps(result.scene)
    assert [event.path.split("/")[-1] for event in result.report.events] == ["first"]
