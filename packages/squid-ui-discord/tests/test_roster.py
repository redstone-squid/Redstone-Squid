"""Host-owned roster allocation and semantic rendering."""

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

import squid_ui_discord
import squid_ui as sl
import squid_ui_widgets as sp
from squid_ui import scene
from squid_ui.chrome import Chrome
from squid_ui.runtime.component import render_component_tree

SLOTS = (
    sp.RosterSlot("tank", "Tank", capacity=1),
    sp.RosterSlot("healer", "Healer", capacity=1, tone=sl.Tone.SUCCESS),
)


def _entry(actor: str, slot: str, minute: int | None) -> sp.RosterEntry:
    joined_at = None if minute is None else datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minute)
    return sp.RosterEntry(actor, actor.title(), slot, joined_at)


def _walk(value: object):
    yield value
    if isinstance(value, tuple):
        for item in value:
            yield from _walk(item)
    elif is_dataclass(value):
        for item in fields(value):
            yield from _walk(getattr(value, item.name))


def test_allocation_is_fifo_and_promotion_follows_from_reallocation() -> None:
    alice = _entry("alice", "tank", 0)
    bob = _entry("bob", "tank", 1)
    undated = _entry("charlie", "tank", None)

    placement = sp.place_roster((undated, bob, alice), SLOTS)

    assert placement.group("tank").members == (alice,)
    assert placement.waitlist == (bob, undated)
    assert placement.status("alice") is sp.RosterStatus.SEATED
    assert placement.status("bob") is sp.RosterStatus.WAITLISTED
    assert sp.place_roster((bob, undated), SLOTS).group("tank").members == (bob,)


def test_reject_overflow_is_retained_as_an_explicit_outcome() -> None:
    alice = _entry("alice", "tank", 0)
    bob = _entry("bob", "tank", 1)

    placement = sp.place_roster(
        (alice, bob),
        SLOTS,
        overflow=sp.RosterOverflow.REJECT,
    )

    assert placement.waitlist == ()
    assert placement.rejected == (bob,)
    assert placement.status("bob") is sp.RosterStatus.REJECTED


@pytest.mark.parametrize(
    ("entries", "slots", "message"),
    [
        ((_entry("alice", "missing", 0),), SLOTS, "unknown slot"),
        ((_entry("alice", "tank", 0), _entry("alice", "healer", 1)), SLOTS, "only once"),
        ((), (SLOTS[0], SLOTS[0]), "keys must be unique"),
    ],
)
def test_allocation_rejects_invalid_ledgers(entries, slots, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        sp.place_roster(entries, slots)


def test_roster_uses_active_chrome_and_renders_routed_slots() -> None:
    placement = sp.place_roster(
        (_entry("alice", "tank", 0), _entry("bob", "tank", 1)),
        SLOTS,
        overflow=sp.RosterOverflow.REJECT,
    )
    node = sl.roster(
        placement,
        key="raid",
        routes={"tank": "raid:tank", "healer": "raid:healer"},
    )
    result = sl.planning.plan(
        node,
        target=squid_ui_discord.DISCORD_V2_DPY27,
        chrome=Chrome(
            waitlist="Queue", full="No seats", slot_count=lambda count, capacity: f"Seats {count}/{capacity}"
        ),
    )
    text = [item.content for item in _walk(result.scene.body) if isinstance(item, scene.Text)]
    buttons = [item for item in _walk(result.scene.body) if isinstance(item, scene.RoutedButton)]

    assert "### Tank — Seats 1/1" in text
    assert "No seats" in text
    assert [button.route_id for button in buttons] == ["raid:tank", "raid:healer"]


async def test_mounted_roster_delivers_one_portable_slot_key() -> None:
    seen: list[sl.SelectionEvent] = []

    async def join(event: sl.SelectionEvent) -> None:
        seen.append(event)

    result = sl.planning.plan(
        sl.roster(sp.place_roster((), SLOTS), key="raid", on_join=join),
        target=squid_ui_discord.DISCORD_V2_DPY27,
    )
    button = next(item for item in _walk(result.scene.body) if isinstance(item, scene.Button))
    await result.bindings[button.action].handler(
        sl.PressEvent(sl.interactions.Actor("7"), cast(sl.interactions.ActionResponder, object()))
    )

    assert seen[0].values == ("tank",)


def test_roster_validates_dispatch_modes_and_namespaces_its_controls() -> None:
    placement = sp.place_roster((), SLOTS)

    async def join(_event: sl.SelectionEvent) -> None: ...

    with pytest.raises(ValueError, match="on_join or routes"):
        sl.roster(placement, key="raid", on_join=join, routes={})
    with pytest.raises(ValueError, match="exactly one route"):
        sl.roster(placement, key="raid", routes={"tank": "raid:tank"})

    class Child(sl.Component):
        def render(self):
            return sl.roster(placement, key="raid", on_join=join)

    class Parent(sl.Component):
        def __init__(self) -> None:
            self.child = Child()

        def render(self):
            return self.boundary(self.child, key="child")

    tree = render_component_tree(Parent())
    roster = cast(sl.semantic.Roster, tree.nodes[0])
    assert roster.key == "child.raid"
