"""Computed candidates and delivered generations have independent lifetimes."""

from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import squid_ui as sl
from squid_ui import scene
from squid_ui.ext.discord import DISCORD_V2_DPY27
from squid_ui.pages import PagePosition
from squid_ui.planning import CompositionOptions, OccurrenceId, PlanningSnapshot, plan
from squid_ui.planning.result import PlanReuse
from squid_ui.presentation import PresentationState


async def ignored(event: sl.PressEvent) -> None:
    pass


def test_exact_replay_skips_preparation_and_only_delivery_commits() -> None:
    document = sl.Document((sl.paragraph("Hello"),))
    session = PresentationState()
    snapshot = PlanningSnapshot[scene.ComponentsV2]()
    first = plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot)
    assert snapshot.committed is None
    with patch("squid_ui.planning.composition_planner.prepare_composition", side_effect=AssertionError):
        retry = plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot)
    assert retry.metrics.reuse is PlanReuse.EXACT
    assert snapshot.committed is None
    session.apply_updates(retry.session_updates)
    snapshot.commit(retry, session)
    assert snapshot.committed is retry
    replay = plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot)
    assert replay.metrics.reuse is PlanReuse.EXACT
    # An older in-flight result cannot be promoted as the current candidate.
    snapshot.commit(first, session)
    assert snapshot.committed is None
    snapshot.clear()
    assert snapshot.fingerprints.snapshot().entries == 0


def test_mutated_position_mapping_invalidates_exact_replay() -> None:
    document = sl.Document((sl.region(sl.paragraph("First"), sl.paragraph("Second"), key="r", title="Region"),))
    options = CompositionOptions(automatic=True, partitions={OccurrenceId(("root", "r")): (1, 2)})
    session = PresentationState()
    snapshot = PlanningSnapshot[scene.ComponentsV2]()
    positions = {"$/r": PagePosition(page=0)}
    first = plan(
        document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot, positions=positions, composition=options
    )
    session.apply_updates(first.session_updates)
    snapshot.commit(first, session)
    positions["$/r"] = PagePosition(page=1)
    second = plan(
        document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot, positions=positions, composition=options
    )
    assert second.metrics.reuse is PlanReuse.ALLOCATION
    assert second.scene != first.scene


@pytest.mark.parametrize("enabled", [True, False])
def test_cold_reference_preserves_presentation_history(enabled: bool) -> None:
    document = sl.Document((sl.paragraph("Hello"),))
    session = PresentationState()
    snapshot = PlanningSnapshot[scene.ComponentsV2](enabled=enabled)
    first = plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot)
    session.apply_updates(first.session_updates)
    snapshot.commit(first, session)
    second = plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot)
    assert second.scene == first.scene
    assert (second.metrics.reuse is PlanReuse.EXACT) is enabled
    if not enabled:
        assert second.metrics.target_evaluations > 0
        assert snapshot.fingerprints.snapshot().entries == 0


@settings(max_examples=30, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 4), st.booleans(), st.integers(0, 1), st.booleans()), min_size=1, max_size=12))
def test_generated_edits_match_cold_planning(edits: list[tuple[int, bool, int, bool]]) -> None:
    sessions = (PresentationState(), PresentationState())
    snapshots = (PlanningSnapshot[scene.ComponentsV2](), PlanningSnapshot[scene.ComponentsV2](enabled=False))
    for version, reverse, page, delivered in edits:
        children = tuple(sl.paragraph(f"Item {key}: {version}", key=str(key)) for key in range(2))
        if reverse:
            children = children[::-1]
        document = sl.Document((sl.region(*children, key="r", title="Region"),))
        options = CompositionOptions(
            automatic=True,
            partitions={OccurrenceId(("root", "r")): (1, 2)},
            pages={OccurrenceId(("root", "r")): page},
        )
        results = [
            plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot, composition=options)
            for session, snapshot in zip(sessions, snapshots, strict=True)
        ]
        cached, cold = results
        assert cached.scene == cold.scene
        assert cached.report.events == cold.report.events
        assert cached.report.regions == cold.report.regions
        assert cached.cost == cold.cost
        assert cached.effects == cold.effects
        assert cached.resources == cold.resources
        assert cached.bindings.keys() == cold.bindings.keys()
        if delivered:
            for session, snapshot, result in zip(sessions, snapshots, results, strict=True):
                session.apply_updates(result.session_updates)
                snapshot.commit(result, session)
        assert sessions[0].cursors == sessions[1].cursors


@pytest.mark.parametrize(("strict", "budget"), [(True, 512), (False, 10_000)])
def test_environment_changes_invalidate_exact_and_allocation_reuse(strict: bool, budget: int) -> None:
    document = sl.Document((sl.paragraph("Hello"),))
    session = PresentationState()
    snapshot = PlanningSnapshot[scene.ComponentsV2]()
    first = plan(document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot)
    session.apply_updates(first.session_updates)
    snapshot.commit(first, session)
    second = plan(
        document, target=DISCORD_V2_DPY27, session=session, snapshot=snapshot, strict=strict, search_budget=budget
    )
    assert second.metrics.target_evaluations > 0
    assert snapshot.statistics().last_miss == "environment"
    assert snapshot.statistics().hits == 0
