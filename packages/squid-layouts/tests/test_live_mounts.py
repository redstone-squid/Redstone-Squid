"""The weak registry behind `sl.discord.mounts()`: who is live, and for how long."""

import gc

import pytest

from squid_layouts import Component, PressEvent, state
from squid_layouts.discord import DeliveryReceipt, Everyone, Mount, Owner, live
from squid_layouts.discord.testing import commit_render, delivered_to, fake_interaction, fake_message
from squid_layouts.primitives import Button, Heading, Row, Text


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
    def test_an_unsent_mount_is_not_live(self) -> None:
        Mount(Panel(), access=Everyone())

        assert live.mounts() == ()

    async def test_a_delivered_mount_is_live(self) -> None:
        mount = Mount(Panel(), access=Everyone())
        await mount.send(delivered_to(fake_message()))

        assert live.mounts() == (mount,)
        assert live.find(mount.id) is mount

    def test_registration_is_idempotent_across_renders(self) -> None:
        mount = Mount(Panel(), access=Everyone())
        commit_render(mount)
        commit_render(mount)

        assert live.mounts() == (mount,)
        # One hook, not one per generation: the mount is deregistered once when it finishes.
        assert len(mount._finish_hooks) == 1

    async def test_finishing_deregisters_before_collection(self) -> None:
        mount = Mount(Panel(), access=Everyone())
        await mount.send(delivered_to(fake_message()))
        await mount.finish()

        # Still strongly referenced here, so anything left in the registry would be a lie
        # that survives until the local goes out of scope.
        assert live.mounts() == ()
        assert mount.finished

    def test_a_collected_mount_leaves_no_entry(self) -> None:
        commit_render(Mount(Panel(), access=Everyone()))
        gc.collect()

        assert live.mounts() == ()

    def test_ordering_follows_first_render(self) -> None:
        first, second = Mount(Panel(), access=Everyone()), Mount(Panel(), access=Everyone())
        commit_render(second)
        commit_render(first)

        assert [mount.id for mount in live.mounts()] == [second.id, first.id]

    def test_find_misses_an_unknown_id(self) -> None:
        assert live.find("nope") is None


class TestSnapshot:
    async def test_a_delivered_mount_knows_where_it_is(self) -> None:
        mount = Mount(Panel(), access=Everyone())
        await mount.send(delivered_to(fake_message(message_id=42, channel_id=5, guild_id=7)))
        snapshot = mount.snapshot()

        assert snapshot.address is not None
        assert (snapshot.address.message_id, snapshot.address.channel_id, snapshot.address.guild_id) == (42, 5, 7)
        assert not snapshot.address.ephemeral
        assert snapshot.address.jump_url.endswith("/7/5/42")

    async def test_a_handleless_mount_learns_its_message_from_the_first_click(self) -> None:
        mount = Mount(Panel(), access=Everyone())
        # A destination that delivers without handing a message back — the unwaited
        # interaction response, where the mount runs located only once someone clicks.
        await mount.send(lambda view, files: _none())
        assert mount.snapshot().address is None

        await mount.dispatch("inc", fake_interaction(message_id=42))
        address = mount.snapshot().address

        assert address is not None
        assert address.message_id == 42

    def test_a_snapshot_describes_the_committed_generation(self) -> None:
        mount = Mount(Panel(), access=Owner(7), timeout=900)
        commit_render(mount)
        snapshot = mount.snapshot()

        assert snapshot.id == mount.id
        assert snapshot.component.endswith("Panel")
        assert snapshot.generation == 1
        assert snapshot.handler_keys == ("inc",)
        assert snapshot.access == Owner(7)
        assert not snapshot.pending and not snapshot.finished
        assert snapshot.scene is not None and snapshot.report is not None and snapshot.metrics is not None
        assert snapshot.expires_in is not None and 0 < snapshot.expires_in <= 900

    def test_an_unrendered_mount_has_no_plan(self) -> None:
        snapshot = Mount(Panel(), access=Everyone(), timeout=None).snapshot()

        assert (snapshot.scene, snapshot.report, snapshot.metrics) == (None, None, None)
        assert snapshot.generation == 0
        assert snapshot.expires_in is None

    def test_pending_state_changes_show_as_dirty(self) -> None:
        panel = Panel()
        mount = Mount(panel, access=Everyone())
        commit_render(mount)
        panel.count += 1

        assert mount.snapshot().pending


async def _none() -> DeliveryReceipt:
    return DeliveryReceipt(None, None)
