"""Review histories must never undo another seat's contribution."""

import squid_ui as sl
from squid.bot.layout_review import Reviewer, ReviewerPanel, ReviewRoom, ReviewSummary
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler, Owner
from squid_ui_discord.sessions import UserScope
from squid_ui_discord.testing import (
    assert_within_limits,
    commit_render,
    delivered_to,
    interaction_harness,
    message_harness,
)


def room_panels() -> tuple[ReviewRoom, MessageRoot, MessageRoot, MessageRoot, MessageRootScheduler]:
    bus = sl.runtime.LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    room = ReviewRoom(bus, UserScope(7))
    room.seats = tuple(Reviewer(bus, UserScope(7), name) for name in ("Alice", "Bob"))
    alice, bob = (ReviewerPanel(room, seat, simulated=True) for seat in room.seats)
    return (
        room,
        MessageRoot(alice, access=Owner(7), scheduler=scheduler, timeout=None),
        MessageRoot(bob, access=Owner(7), scheduler=scheduler, timeout=None),
        MessageRoot(ReviewSummary(room, bus, 7, "simulated"), access=Everyone(), scheduler=scheduler, timeout=None),
        scheduler,
    )


async def test_independent_undo_and_cross_message_updates() -> None:
    room, alice, bob, summary, scheduler = room_panels()
    for i, root in enumerate((alice, bob, summary), 1):
        await root.send(delivered_to(message_harness(message_id=i)))
    await alice.dispatch("approve", interaction_harness(user_id=7))
    await bob.dispatch("approve", interaction_harness(user_id=7))
    assert room.totals().startswith("2 approved")
    assert summary in scheduler._queued
    assert alice in scheduler._queued
    await alice.dispatch("undo", interaction_harness(user_id=7))
    assert room.seats[0].review.decision == "Pending"
    assert room.seats[1].review.decision == "Approved"
    assert room.totals().startswith("1 approved")
    await alice.dispatch("redo", interaction_harness(user_id=7))
    assert room.totals().startswith("2 approved")
    assert len(room.activity) == 4
    for root in (alice, bob, summary):
        assert_within_limits(commit_render(root))


async def test_owner_access_and_closed_room_prevent_edits() -> None:
    room, alice, _, _, _ = room_panels()
    await alice.send(delivered_to(message_harness()))
    await alice.dispatch("approve", interaction_harness(user_id=99))
    assert room.seats[0].review.decision == "Pending"
    room.closed = True
    await alice.dispatch("approve", interaction_harness(user_id=7))
    assert room.seats[0].review.decision == "Pending"


def test_invocations_are_isolated_and_activity_bounded() -> None:
    room, _, _, _, _ = room_panels()
    other, _, _, _, _ = room_panels()
    for i in range(20):
        room.log(str(i))
    assert room.activity == tuple(str(i) for i in range(14, 20))
    assert other.activity == ()
