"""The weak registry behind `squid_ui_discord.message_roots()`: who is live, and for how long."""

import gc

import pytest

from squid_ui import Component, PressEvent, state
from squid_ui.primitives import Button, Heading, Row, Text
from squid_ui_discord import Everyone, MessageRoot, Owner, live
from squid_ui_discord.delivery import DeliveryResult
from squid_ui_discord.testing import commit_render, delivered_to, fake_interaction, fake_message


class Panel(Component):
    count: int = state(0)

    def render(self):
        return [
            Heading("Panel"),
            Text(f"count: {self.count}"),
            Row((Button(label="+1", on_click=self.bump, key="inc"),)),
        ]

    async def bump(self, event: PressEvent) -> None:
        self.count += 1


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Every test starts from an empty process registry and leaves one behind."""
    live._LIVE.clear()
    yield
    live._LIVE.clear()


class TestLiveRegistry:
    def test_an_unsent_message_root_is_not_live(self) -> None:
        MessageRoot(Panel(), access=Everyone())

        assert live.message_roots() == ()

    async def test_a_delivered_message_root_is_live(self) -> None:
        message_root = MessageRoot(Panel(), access=Everyone())
        await message_root.send(delivered_to(fake_message()))

        assert live.message_roots() == (message_root,)
        assert live.find(message_root.id) is message_root

    def test_registration_is_idempotent_across_renders(self) -> None:
        message_root = MessageRoot(Panel(), access=Everyone())
        commit_render(message_root)
        commit_render(message_root)

        assert live.message_roots() == (message_root,)
        # One hook, not one per generation: the mount is deregistered once when it finishes.
        assert len(message_root._finish_hooks) == 1

    async def test_finishing_deregisters_before_collection(self) -> None:
        message_root = MessageRoot(Panel(), access=Everyone())
        await message_root.send(delivered_to(fake_message()))
        await message_root.finish()

        # Still strongly referenced here, so anything left in the registry would be a lie
        # that survives until the local goes out of scope.
        assert live.message_roots() == ()
        assert message_root.finished

    def test_a_collected_message_root_leaves_no_entry(self) -> None:
        commit_render(MessageRoot(Panel(), access=Everyone()))
        gc.collect()

        assert live.message_roots() == ()

    def test_ordering_follows_first_render(self) -> None:
        first, second = MessageRoot(Panel(), access=Everyone()), MessageRoot(Panel(), access=Everyone())
        commit_render(second)
        commit_render(first)

        assert [message_root.id for message_root in live.message_roots()] == [second.id, first.id]

    def test_find_misses_an_unknown_id(self) -> None:
        assert live.find("nope") is None


class TestSnapshot:
    async def test_a_delivered_message_root_knows_where_it_is(self) -> None:
        message_root = MessageRoot(Panel(), access=Everyone())
        await message_root.send(delivered_to(fake_message(message_id=42, channel_id=5, guild_id=7)))
        snapshot = message_root.snapshot()

        assert snapshot.address is not None
        assert (snapshot.address.message_id, snapshot.address.channel_id, snapshot.address.guild_id) == (42, 5, 7)
        assert not snapshot.address.ephemeral
        assert snapshot.address.jump_url.endswith("/7/5/42")

    async def test_a_handleless_message_root_learns_its_message_from_the_first_click(self) -> None:
        message_root = MessageRoot(Panel(), access=Everyone())
        # A destination that delivers without handing a message back — the unwaited
        # interaction response, where the mount runs located only once someone clicks.
        await message_root.send(lambda presentation: _none())
        assert message_root.snapshot().address is None

        await message_root.dispatch("inc", fake_interaction(message_id=42))
        address = message_root.snapshot().address

        assert address is not None
        assert address.message_id == 42

    def test_a_snapshot_describes_the_committed_generation(self) -> None:
        message_root = MessageRoot(Panel(), access=Owner(7), timeout=900)
        commit_render(message_root)
        snapshot = message_root.snapshot()

        assert snapshot.id == message_root.id
        assert snapshot.component.endswith("Panel")
        assert snapshot.generation == 1
        assert snapshot.handler_keys == ("inc",)
        assert snapshot.access == Owner(7)
        assert not snapshot.pending and not snapshot.finished
        assert snapshot.scene is not None and snapshot.report is not None and snapshot.metrics is not None
        assert snapshot.expires_in is not None and 0 < snapshot.expires_in <= 900

    def test_an_unrendered_message_root_has_no_plan(self) -> None:
        snapshot = MessageRoot(Panel(), access=Everyone(), timeout=None).snapshot()

        assert (snapshot.scene, snapshot.report, snapshot.metrics) == (None, None, None)
        assert snapshot.generation == 0
        assert snapshot.expires_in is None

    def test_pending_state_changes_show_as_dirty(self) -> None:
        panel = Panel()
        message_root = MessageRoot(panel, access=Everyone())
        commit_render(message_root)
        panel.count += 1

        assert message_root.snapshot().pending


async def _none() -> DeliveryResult:
    return DeliveryResult(None, None)
