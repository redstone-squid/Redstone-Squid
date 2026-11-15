"""Roster pagination preserves slot context, membership, and join behavior."""

from dataclasses import replace
from typing import Any

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.authoring.rosters import RosterEntry, RosterOverflow, RosterSlot, place_roster
from squid_ui.chrome import Chrome
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "roster.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "roster.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    squid_ui.ext.html.target(),
    squid_ui.ext.slack.message_target(adapter=SLACK),
    squid_ui.ext.slack.home_target(adapter=SLACK),
]
CHROME = Chrome(slot_count=lambda count, capacity: f"Seats {count}/{capacity}", full="No seats", waitlist="Queue")


async def join(event: sl.SelectionEvent) -> None:
    pytest.fail("planning invoked a join handler")


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_roster_pages_repeat_localized_slot_context_and_join_controls(target: Target[Any, Any, Any, Any]) -> None:
    members = tuple(RosterEntry(str(index), f"Member {index}", "tank") for index in range(4))
    placement = place_roster(members, (RosterSlot("tank", "Tank", capacity=8),))
    node = sl.region(sl.roster(placement, key="raid", on_join=join), key="r", title="R")
    identity = OccurrenceId(("root", "r"))
    result = plan(
        node,
        target=target,
        chrome=CHROME,
        strict=True,
        composition=CompositionOptions(automatic=True, partitions={identity: (2, 4)}),
    )
    assert result.allocation is not None
    assert len(result.allocation.regions) == 1
    for index in range(2):
        page = result.for_page({identity: index})
        wire = (
            squid_ui.ext.html.Renderer().draw(page.scene, plan=page)
            if isinstance(page.scene.body, scene.HtmlBody)
            else scene.Codec.dumps(page.scene)
        )
        assert "Tank — Seats 4/8" in wire
        assert f"Member {index * 2}" in wire and f"Member {index * 2 + 1}" in wire
        assert f"Member {(1 - index) * 2}" not in wire
        assert "raid.tank" in page.bindings
        assert not page.report.events


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_roster_waitlist_is_visible_without_changing_join_availability(target: Target[Any, Any, Any, Any]) -> None:
    placement = place_roster(
        (RosterEntry("a", "Alice", "tank"), RosterEntry("b", "Bob", "tank")),
        (RosterSlot("tank", "Tank", capacity=1),),
    )
    node = sl.roster(placement, key="raid", on_join=join)
    result = plan(node, target=target, chrome=CHROME)
    assert result.allocation is not None
    wire = (
        squid_ui.ext.html.Renderer().draw(result.scene, plan=result)
        if isinstance(result.scene.body, scene.HtmlBody)
        else scene.Codec.dumps(result.scene)
    )
    assert "Tank — Seats 1/1" in wire and "Queue" in wire and "Alice" in wire and "Bob" in wire
    assert "No seats" not in wire
    assert "raid.tank" in result.bindings
    hidden = plan(replace(node, show_waitlist=False), target=target, chrome=CHROME)
    assert "Bob" not in scene.Codec.dumps(hidden.scene)


@pytest.mark.parametrize("target", TARGETS[:3], ids=lambda target: target.id)
def test_full_rejecting_slots_disable_join_without_discarding_slot_counts(target: Target[Any, Any, Any, Any]) -> None:
    placement = place_roster(
        (RosterEntry("a", "Alice", "tank"),), (RosterSlot("tank", "Tank", capacity=1),), overflow=RosterOverflow.REJECT
    )
    result = plan(sl.roster(placement, key="raid", on_join=join), target=target, chrome=CHROME)
    wire = (
        squid_ui.ext.html.Renderer().draw(result.scene, plan=result)
        if isinstance(result.scene.body, scene.HtmlBody)
        else scene.Codec.dumps(result.scene)
    )
    assert "No seats" in wire and "Seats 1/1" in wire
    if isinstance(result.scene.body, scene.HtmlBody):
        assert "disabled" in squid_ui.ext.html.Renderer().draw(result.scene, plan=result)
    else:
        assert '"disabled":true' in wire.replace(" ", "")


@pytest.mark.parametrize("target", TARGETS[3:], ids=lambda target: target.id)
def test_locked_roster_reports_slack_disabled_control_capability(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.roster(place_roster((), (RosterSlot("tank", "Tank"),)), key="raid", on_join=join, locked=True)
    with pytest.raises(JointUnsupportedError):
        plan(sl.region(node, key="r", title="R"), target=target)


def test_roster_spill_counts_omitted_members_and_keeps_the_join_control() -> None:
    placement = place_roster(
        tuple(RosterEntry(str(index), "Member " + "x" * 30, "tank") for index in range(3)),
        (RosterSlot("tank", "Tank"),),
    )
    node = sl.roster(placement, key="raid", on_join=join)
    target = components_v2_target(DISCORD, limits=V2Limits(total_text=50))
    result = plan(node, target=target)
    assert result.allocation is not None
    assert "raid.tank" in result.bindings
    assert any(event.message == "Omission" for event in result.report.events)
    with pytest.raises(JointInfeasibleError):
        plan(node, target=target, strict=True)


def test_roster_membership_and_availability_changes_invalidate_reuse() -> None:
    session = PresentationState()
    node = sl.roster(place_roster((), (RosterSlot("tank", "Tank"),)), key="raid", on_join=join)
    first = plan(node, target=TARGETS[0], session=session)
    session.apply_updates(first.session_updates)
    assert plan(node, target=TARGETS[0], session=session).allocation is first.allocation
    changed = plan(replace(node, locked=True), target=TARGETS[0], session=session)
    assert changed.allocation is not first.allocation
