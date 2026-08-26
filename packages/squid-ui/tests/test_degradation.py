"""Structured degradation ranking and measured-layout accounting."""

from squid_ui.planning import measure
from squid_ui.planning.degradation import DegradationEffect, DegradationProfile
from squid_ui.primitives import Drop, Text, Truncate, alts


def profile(effect: DegradationEffect) -> DegradationProfile:
    return DegradationProfile.from_effects([effect])


def test_lower_priority_loss_beats_any_higher_priority_loss() -> None:
    low_drop = profile(DegradationEffect(-10, "$.low", dropped_nodes=1))
    high_semantic_step = profile(DegradationEffect(10, "$.high", semantic_steps=1))

    assert low_drop < high_semantic_step


def test_semantic_steps_beat_truncation_spill_and_whole_node_drop() -> None:
    semantic = profile(DegradationEffect(0, "$.node", semantic_steps=10))
    truncated = profile(DegradationEffect(0, "$.node", truncated_chars=1))
    spilled = profile(DegradationEffect(0, "$.node", spilled_items=1))
    dropped = profile(DegradationEffect(0, "$.node", dropped_nodes=1))

    assert semantic < truncated < spilled < dropped


def test_measurement_accounts_for_the_selected_overflow_policy() -> None:
    semantic = measure([Text("x" * 5000, overflow=alts("summary"))])
    truncated = measure([Text("x" * 5000, overflow=Truncate())])
    dropped = measure([Text("x" * 5000, overflow=Drop())])

    assert semantic.degradation < truncated.degradation < dropped.degradation
