"""Session policy: instance limits per key, and parent/child lifetime.

Stub mounts throughout -- the registry never touches Discord, which is the point of keeping
rejection wording at the call sites.
"""

from typing import Any

import anyio
import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import MountRegistry, SessionKey, WhenOpen
from squid_layouts.discord.testing import fake_message
from squid_layouts.primitives import Button, Heading, Row


class Panel(sl.Component):
    """The smallest thing with a control on it."""

    def render(self):
        return [Heading("Panel"), Row((Button(label="+1", on_click=self._noop, key="go"),))]

    async def _noop(self, event: sl.PressEvent) -> None:
        return None


def a_mount() -> sl.discord.Mount:
    return sl.discord.Mount(Panel(), timeout=None)


_DEFAULT_MESSAGE = object()


def to_message(message: Any = _DEFAULT_MESSAGE) -> sl.discord.Destination:
    """A destination that delivers and hands back credentials, with no Discord in it."""
    delivered = fake_message() if message is _DEFAULT_MESSAGE else message

    async def send(view: discord.ui.LayoutView, files: list[discord.File]):
        handle = None if delivered is None else sl.discord.delivery.handle_for(delivered)
        return sl.discord.DeliveryReceipt(delivered, handle)

    return send


def slowly() -> sl.discord.Destination:
    """A destination with a checkpoint in it, so two opens genuinely interleave."""

    async def send(view: discord.ui.LayoutView, files: list[discord.File]):
        await anyio.sleep(0)
        message = fake_message()
        return sl.discord.DeliveryReceipt(message, sl.discord.delivery.handle_for(message))

    return send


def abandoning() -> sl.discord.Destination:
    """A destination that deliberately delivers nothing, as a closed DM does."""

    async def send(view: discord.ui.LayoutView, files: list[discord.File]):
        raise sl.discord.DeliveryAbandoned

    return send


def failing(error: Exception) -> sl.discord.Destination:
    async def send(view: discord.ui.LayoutView, files: list[discord.File]):
        raise error

    return send


KEY = SessionKey("panel", user_id=7, scope=42)


async def test_any_hashable_key_can_name_a_session() -> None:
    registry = MountRegistry()
    key = ("panel", "team-red", 42)
    mount = a_mount()

    await registry.open(mount, to_message(), key=key)

    assert registry.get(key) is mount


class TestReplace:
    async def test_the_incumbent_is_finished_and_the_newcomer_holds_the_key(self):
        registry = MountRegistry()
        first, second = a_mount(), a_mount()

        await registry.open(first, to_message(), key=KEY)
        await registry.open(second, to_message(), key=KEY)

        assert first.finished
        assert not second.finished
        assert registry.get(KEY) is second

    async def test_the_incumbent_survives_a_failed_send(self):
        registry = MountRegistry()
        first, second = a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY)

        with pytest.raises(RuntimeError):
            await registry.open(second, failing(RuntimeError("gateway is down")), key=KEY)

        assert not first.finished
        assert registry.get(KEY) is first

    async def test_the_incumbent_survives_an_abandoned_send(self):
        """`Mount.send` swallows `DeliveryAbandoned` and returns `None`, which is also what a
        successful handle-less delivery returns. Reading the wrong one costs the user both
        panels and leaves no message explaining why."""
        registry = MountRegistry()
        first, second = a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY)

        opened = await registry.open(second, abandoning(), key=KEY)

        assert opened is None
        assert not first.finished
        assert registry.get(KEY) is first

    async def test_a_handle_less_delivery_still_replaces(self):
        """The other `None`: delivered, but no credentials came back."""
        registry = MountRegistry()
        first, second = a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY)

        opened = await registry.open(second, to_message(message=None), key=KEY)

        assert opened is second
        assert first.finished
        assert registry.get(KEY) is second

    async def test_cleanup_is_identity_checked(self):
        """The incumbent's own hook fires against a key the newcomer already owns."""
        registry = MountRegistry()
        first, second = a_mount(), a_mount()

        await registry.open(first, to_message(), key=KEY)
        await registry.open(second, to_message(), key=KEY)

        assert registry.get(KEY) is second

    async def test_the_last_session_leaves_no_entry_behind(self):
        registry = MountRegistry()
        mount = a_mount()
        await registry.open(mount, to_message(), key=KEY)

        await mount.finish()

        assert registry.get(KEY) is None


class TestReject:
    async def test_a_second_open_delivers_nothing(self):
        registry = MountRegistry()
        first, second = a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY, policy=WhenOpen.REJECT)

        opened = await registry.open(second, to_message(), key=KEY, policy=WhenOpen.REJECT)

        assert opened is None
        assert not first.finished
        assert not second.finished
        assert registry.get(KEY) is first

    async def test_the_key_frees_up_once_the_incumbent_finishes(self):
        registry = MountRegistry()
        first, second = a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY, policy=WhenOpen.REJECT)
        await first.finish()

        opened = await registry.open(second, to_message(), key=KEY, policy=WhenOpen.REJECT)

        assert opened is second

    async def test_a_stale_finished_entry_does_not_lock_the_key_forever(self):
        """Defence in depth: `on_finish` should have cleared it, and a REJECT lockout would
        otherwise last the life of the process."""
        registry = MountRegistry()
        first, second = a_mount(), a_mount()
        await registry.open(first, to_message(), key=KEY, policy=WhenOpen.REJECT)
        first._finished = True  # finished without its hooks running

        opened = await registry.open(second, to_message(), key=KEY, policy=WhenOpen.REJECT)

        assert opened is second


class TestRacingOpens:
    async def test_two_concurrent_opens_leave_one_survivor(self):
        registry = MountRegistry()
        mounts = [a_mount() for _ in range(2)]

        # The destination yields, so both opens are inside `open` before either registers --
        # without the per-key lock both would see no incumbent and both would survive.
        async with anyio.create_task_group() as tasks:
            for mount in mounts:
                tasks.start_soon(lambda m=mount: registry.open(m, slowly(), key=KEY))

        assert sum(not mount.finished for mount in mounts) == 1
        survivor = registry.get(KEY)
        assert survivor is not None
        assert not survivor.finished

    async def test_the_lock_map_empties_once_the_key_goes_idle(self):
        registry = MountRegistry()

        await registry.open(a_mount(), to_message(), key=KEY)

        assert registry._locks == {}
        assert registry._waiting == {}


class TestCascade:
    async def test_a_child_dies_with_its_parent(self):
        registry = MountRegistry()
        parent, child = a_mount(), a_mount()
        await registry.open(parent, to_message(), key=KEY)
        await registry.open(child, to_message(), parent=parent)

        await parent.finish()

        assert child.finished

    async def test_a_grandchild_dies_too(self):
        registry = MountRegistry()
        parent, child, grandchild = a_mount(), a_mount(), a_mount()
        await registry.open(parent, to_message())
        await registry.open(child, to_message(), parent=parent)
        await registry.open(grandchild, to_message(), parent=child)

        await parent.finish()

        assert child.finished
        assert grandchild.finished

    async def test_an_unregistered_parent_still_cascades(self):
        """A panel not yet migrated to the registry is still a perfectly good parent."""
        registry = MountRegistry()
        parent, child = a_mount(), a_mount()
        await registry.open(child, to_message(), parent=parent)

        await parent.finish()

        assert child.finished

    async def test_a_child_of_an_already_finished_parent_is_finished_at_once(self):
        registry = MountRegistry()
        parent, child = a_mount(), a_mount()
        await parent.finish()

        await registry.open(child, to_message(), parent=parent)

        assert child.finished

    async def test_a_timing_out_parent_cascades(self):
        registry = MountRegistry()
        parent, child = a_mount(), a_mount()
        await registry.open(child, to_message(), parent=parent)

        await parent.handle_timeout()

        assert child.finished

    async def test_one_unreachable_child_does_not_strand_its_siblings(self):
        registry = MountRegistry()
        parent, doomed, sibling = a_mount(), a_mount(), a_mount()
        await registry.open(doomed, to_message(), parent=parent)
        await registry.open(sibling, to_message(), parent=parent)

        async def explode(mount: sl.discord.Mount) -> None:
            raise RuntimeError("message is gone")

        doomed.on_finish(explode)

        await parent.finish()

        assert sibling.finished


class TestCloseAll:
    async def test_every_session_is_finished(self):
        registry = MountRegistry()
        keyed, parented = a_mount(), a_mount()
        parent = a_mount()
        await registry.open(keyed, to_message(), key=KEY)
        await registry.open(parented, to_message(), parent=parent)

        await registry.close_all()

        assert keyed.finished
        assert parented.finished
        assert registry.get(KEY) is None

    async def test_close_finishes_one_key(self):
        registry = MountRegistry()
        mount = a_mount()
        await registry.open(mount, to_message(), key=KEY)

        await registry.close(KEY)

        assert mount.finished

    async def test_close_on_an_empty_key_is_a_no_op(self):
        await MountRegistry().close(KEY)

    async def test_active_reports_a_mount_once_even_when_keyed_and_parented(self):
        registry = MountRegistry()
        parent, mount = a_mount(), a_mount()
        await registry.open(mount, to_message(), key=KEY, parent=parent)

        assert [key for key, _ in registry.active()] == [KEY]
