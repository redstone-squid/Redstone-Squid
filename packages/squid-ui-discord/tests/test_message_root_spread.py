"""One message root can spread a crowded component over several Discord messages.

Every test mounts the same component under a reduced component limit, so its document
needs two or three messages. Recorded message and interaction calls stand in for Discord.
"""

from dataclasses import replace
from typing import Any, override

import discord
import pytest

import squid_ui as sl
from squid_ui import Component, PressEvent, state
from squid_ui.primitives import Button, Row
from squid_ui_discord import Everyone, MessageRoot
from squid_ui_discord.delivery import DeliveryResult, MessageDestination, handle_for
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.target import DISCORD_V2_DPY27
from squid_ui_discord.testing import interaction_harness, message_harness, payload_texts

# A paragraph costs one component and a button row two, so a message holds four paragraphs.
TARGET = replace(DISCORD_V2_DPY27, limits=replace(DISCORD_V2_DPY27.limits, total_components=4))


class Panel(Component[sl.ComponentsV2Target]):
    """Two paragraphs and a button, then `notes` paragraphs and a second button."""

    count: int = state(0)
    notes: int = state(1)
    title: str = state("Panel")

    @override
    def render(self) -> list[Any]:
        return [
            sl.paragraph(self.title, key="title"),
            Row((Button(label="Rename", on_click=self.rename, key="rename"),)),
            *(sl.paragraph(f"Note {index}", key=f"note-{index}") for index in range(self.notes)),
            sl.paragraph(f"count: {self.count}", key="count"),
            Row((Button(label="+1", on_click=self.increment, key="inc"),)),
        ]

    async def rename(self, event: PressEvent) -> None:
        self.title = "Renamed"

    async def increment(self, event: PressEvent) -> None:
        self.count += 1

    async def grow(self, event: PressEvent) -> None:
        self.notes += 3


class Delivery:
    """A destination that hands out message harnesses, first to last."""

    def __init__(self, *, follow_ups: bool = True) -> None:
        self.messages = [message_harness(message_id=100 + index) for index in range(4)]
        self.sent: list[MessagePayload] = []
        self._follow_ups = follow_ups

    @property
    def destination(self) -> MessageDestination:
        async def send(payload: MessagePayload) -> DeliveryResult:
            message = self.messages[len(self.sent)]
            self.sent.append(payload)
            return DeliveryResult(message, handle_for(message), follow_up=send if self._follow_ups else None)

        return send

    def texts(self, index: int) -> list[str]:
        """The texts message `index` shows: its last edit, or what it was sent with."""
        edits = self.messages[index].edits
        view = edits[-1].kwargs["view"] if edits else self.sent[index].view
        assert isinstance(view, discord.ui.LayoutView)
        return payload_texts(view)


async def mounted(delivery: Delivery, **state_values: Any) -> tuple[MessageRoot, Panel]:
    panel = Panel()
    for name, value in state_values.items():
        setattr(panel, name, value)
    root = MessageRoot(panel, access=Everyone(), timeout=None, target=TARGET, max_messages=3)
    await root.send(delivery.destination)
    return root, panel


def press_from(delivery: Delivery, index: int) -> Any:
    """An interaction pressed on message `index`."""
    return interaction_harness(message_id=delivery.messages[index].id)


async def test_a_crowded_render_is_sent_as_consecutive_messages() -> None:
    delivery = Delivery()

    root, _ = await mounted(delivery)

    assert len(delivery.sent) == 2
    assert delivery.texts(0) == ["Panel", "Note 0"]
    assert delivery.texts(1) == ["count: 0"]
    assert set(root.snapshot().handler_keys) == {"rename", "inc"}


async def test_a_single_message_root_still_fails_to_lay_out() -> None:
    root = MessageRoot(Panel(), access=Everyone(), timeout=None, target=TARGET)

    with pytest.raises(sl.errors.LayoutError):
        await root.send(Delivery().destination)


async def test_a_press_edits_only_the_message_it_changed_and_answers_through_it() -> None:
    delivery = Delivery()
    root, _ = await mounted(delivery)
    interaction = press_from(delivery, 1)

    await root.dispatch("inc", interaction)

    # The press's own response edited its message; the first message was left alone.
    assert len(interaction.response.edit_message.records) == 1
    assert payload_texts(interaction.response.edit_message.records[0].kwargs["view"]) == ["count: 1"]
    assert delivery.messages[0].edits == []
    assert interaction.deferrals == []


async def test_a_press_that_changes_another_message_edits_it_and_is_acknowledged() -> None:
    delivery = Delivery()
    root, _ = await mounted(delivery)
    interaction = press_from(delivery, 0)

    # The pressed control is drawn on the second message; only the press arrives from the first.
    await root.dispatch("inc", interaction)

    assert delivery.texts(1) == ["count: 1"]
    assert interaction.response.edit_message.records == []
    assert len(interaction.deferrals) == 1


async def test_controls_on_a_message_that_was_not_rewritten_keep_working() -> None:
    delivery = Delivery()
    root, panel = await mounted(delivery)
    untouched = root._candidates.continuations[0].generation
    await root.dispatch("rename", press_from(delivery, 0), generation=root.generation)
    assert root.generation != untouched

    # The second message still shows controls from the first render.
    await root.dispatch("inc", press_from(delivery, 1), generation=untouched)

    assert panel.count == 1


async def test_growth_sends_another_message_and_shrinking_deletes_it() -> None:
    delivery = Delivery()
    root, panel = await mounted(delivery)
    interaction = press_from(delivery, 1)
    interaction.followup.send.return_value = delivery.messages[2]

    panel.notes = 6
    await root.refresh(interaction)

    assert len(root._candidates.continuations) == 2
    assert len(interaction.followup.send.records) == 1

    panel.notes = 1
    await root.refresh(press_from(delivery, 0))

    assert len(root._candidates.continuations) == 1
    # The third message was sent as a follow-up to the press, so that press deletes it.
    assert delivery.messages[1].deletions == []
    assert len(interaction.deletions) == 1


async def test_finishing_disables_the_controls_of_every_message() -> None:
    delivery = Delivery()
    root, _ = await mounted(delivery)

    await root.finish()

    for index in (0, 1):
        view = delivery.messages[index].edits[-1].kwargs["view"]
        assert all(item.disabled for item in view.walk_children() if hasattr(item, "disabled"))


async def test_a_message_that_could_not_be_sent_arrives_with_the_next_press() -> None:
    delivery = Delivery(follow_ups=False)
    root, _ = await mounted(delivery)
    assert len(delivery.sent) == 1
    assert root._candidates.stale == {1}
    interaction = press_from(delivery, 0)
    interaction.followup.send.return_value = delivery.messages[1]

    await root.dispatch("rename", interaction)

    assert len(interaction.followup.send.records) == 1
    assert root._candidates.stale == frozenset()


async def test_an_unreachable_message_goes_stale_and_is_repainted_when_pressed() -> None:
    delivery = Delivery()
    root, panel = await mounted(delivery)
    root._handle = None  # as for an ephemeral first message nobody pressed for 15 minutes

    await root.dispatch("rename", press_from(delivery, 1))

    assert panel.title == "Renamed"
    assert root._candidates.stale == {0}

    # A press on the stale message repaints it instead of acting on what it no longer shows.
    interaction = press_from(delivery, 0)
    await root.dispatch("rename", interaction, generation=root.generation)

    assert payload_texts(interaction.response.edit_message.records[0].kwargs["view"]) == ["Renamed", "Note 0"]
    assert root._candidates.stale == frozenset()
