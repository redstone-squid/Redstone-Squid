"""Native text permissions expose their losses to the shared planner."""

from typing import Any

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.ext.discord.limits import ClassicLimits, V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError
from squid_ui.planning.target import Target
from squid_ui.primitives import nodes as native
from squid_ui.primitives.constraints import Condense, Drop, Never, Spill, alts

ADAPTER = AdapterProfile(DiscordAdapter, "native-overflow.discord", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(ADAPTER, limits=V2Limits(total_text=10)),
    classic_target(ADAPTER, limits=ClassicLimits(content=10)),
]


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
@pytest.mark.parametrize("overflow", [Condense(), Spill()])
def test_single_text_condensation_and_spill_keep_their_existing_loss_permission(
    target: Target[Any, Any, Any, Any], overflow: Condense | Spill
) -> None:
    node = sl.region(native.Text("abcdefghijk", overflow=overflow), key="r", title="R")
    result = plan(node, target=target)
    assert result.allocation is not None
    if isinstance(overflow, Condense):
        assert "abcdefghi…" in scene.Codec.dumps(result.scene)
        assert [event.message for event in result.report.events] == ["Shortening"]
    else:
        assert "abcdef" not in scene.Codec.dumps(result.scene)
        assert [event.message for event in result.report.events] == ["Omission"]
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_text_replacements_retain_the_preferred_fitting_rung(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Text("Too long to fit here", overflow=alts("First rung", "Second"))
    result = plan(sl.region(node, key="r", title="R"), target=target)
    assert result.allocation is not None
    assert "First rung" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Replacement"]
    with pytest.raises(JointInfeasibleError):
        plan(sl.region(node, key="r", title="R"), target=target, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_exhausted_text_ladder_shortens_its_last_rung(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Text("The preferred content is long", overflow=alts("Another long replacement"))
    result = plan(sl.region(node, key="r", title="R"), target=target)
    assert "Another l…" in scene.Codec.dumps(result.scene)
    assert {event.message for event in result.report.events} == {"Replacement", "Shortening"}


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_fitting_primary_is_lossless_even_with_native_alternates(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Text("Preferred", overflow=alts("Compact"))
    result = plan(sl.region(node, key="r", title="R"), target=target, strict=True)
    assert "Preferred" in scene.Codec.dumps(result.scene)
    assert not result.report.events


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_native_drop_removes_whole_text_and_preserves_other_content(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        native.Heading("Too long to fit", overflow=Drop()), native.Text("Kept", overflow=Never()), key="r", title="R"
    )
    result = plan(node, target=target)
    wire = scene.Codec.dumps(result.scene)
    assert "Kept" in wire and "Too long" not in wire and "## " not in wire
    assert [event.message for event in result.report.events] == ["Omission"]
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_outer_truncation_keeps_its_policy_precedence(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.truncate(native.Text("abcdefghijklmnop", overflow=alts("Compact")))
    result = plan(node, target=target)
    assert "abcdefghi…" in scene.Codec.dumps(result.scene)
    assert [event.message for event in result.report.events] == ["Shortening"]
