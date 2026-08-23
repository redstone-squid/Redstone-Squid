"""Computed values and resources declared on an `sl.runtime.Shared` namespace.

A cell on a namespace is state several mounts share. These are the two derived forms of the
same idea: a computed, which is a pure function of cells and needs no address of its own, and
a resource, which is loaded and therefore can move on its own -- so it publishes.
"""

import asyncio

import anyio
import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount, Reactor
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.runtime.shared import describe


class Prefs(sl.runtime.Shared[int]):
    first: str = sl.state("Ada")
    last: str = sl.state("Lovelace")
    unread: str = sl.state("not looked at")

    @sl.computed
    def full(self) -> str:
        return f"{self.first} {self.last}"


class Catalog(sl.runtime.Shared[int]):
    key: str = sl.state("k1")

    def __init__(self, bus: sl.runtime.TopicBus, scope: int) -> None:
        super().__init__(bus, scope)
        self._loads = 0

    @sl.resource(delivery=sl.runtime.ResourceDelivery.ATOMIC)
    async def entries(self) -> str:
        self._loads += 1
        return f"{self.key}#{self._loads}"


class Reader(sl.Component):
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def render(self):
        match self.catalog.entries.state:
            case sl.runtime.Ready(value=value):
                return sl.paragraph(value)
            case _:
                return sl.paragraph("loading")


def texts(view: discord.ui.LayoutView) -> list[str]:
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


async def mounted(catalog: Catalog, reactor: Reactor, message: object) -> Mount:
    """Send a reader, and hand the mount back so the caller keeps it alive.

    A reactor holds its mounts weakly, so a test that drops the reference is testing whether
    the collector ran rather than whether the refresh works.
    """
    mount = Mount(Reader(catalog), access=Everyone(), scheduler=reactor, timeout=None)
    await mount.send(delivered_to(message))
    return mount


async def drain(reactor: Reactor, bus: sl.runtime.TopicBus) -> None:
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        await bus.drain()
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


@pytest.fixture
def bus() -> sl.runtime.TopicBus:
    return sl.runtime.TopicBus()


# --- Computed on a namespace ----------------------------------------------------------


def test_a_namespace_computed_derives_from_its_own_cells(bus: sl.runtime.TopicBus) -> None:
    prefs = Prefs(bus, 1)
    assert prefs.full == "Ada Lovelace"

    prefs.first = "Grace"

    assert prefs.full == "Grace Lovelace"


def test_two_namespaces_compute_independently(bus: sl.runtime.TopicBus) -> None:
    """The computed is per-instance, like the cells it reads: the handle is the state."""
    one, two = Prefs(bus, 1), Prefs(bus, 2)

    one.first = "Grace"

    assert (one.full, two.full) == ("Grace Lovelace", "Ada Lovelace")


async def test_a_mount_reading_a_namespace_computed_follows_the_cells_behind_it(bus: sl.runtime.TopicBus) -> None:
    """A computed carries no address: what moves is the cells, so those are what to follow."""
    reactor = Reactor(bus)
    prefs = Prefs(bus, 1)

    class Panel(sl.Component):
        def render(self):
            return sl.paragraph(prefs.full)

    message = fake_message()
    mount = Mount(Panel(), access=Everyone(), scheduler=reactor, timeout=None)
    await mount.send(delivered_to(message))

    assert {describe(address) for address in mount.followed} == {"Prefs(1).first", "Prefs(1).last"}

    with sl.runtime.transaction():
        prefs.first = "Grace"
    await drain(reactor, bus)
    await mount.refresh_now()

    assert texts(message.edit.await_args.kwargs["view"]) == ["Grace Lovelace"]


# --- Resource on a namespace ----------------------------------------------------------


async def test_one_namespace_resource_loads_once_for_every_mount_holding_it(bus: sl.runtime.TopicBus) -> None:
    reactor = Reactor(bus)
    catalog = Catalog(bus, 1)

    mounts = [await mounted(catalog, reactor, fake_message(message_id=message_id)) for message_id in (1, 2)]

    assert catalog._loads == 1, "the second mount shared the value rather than loading its own"
    assert len(mounts) == 2


async def test_a_namespace_resource_is_followed_by_its_own_address(bus: sl.runtime.TopicBus) -> None:
    reactor = Reactor(bus)
    catalog = Catalog(bus, 1)
    mount = Mount(Reader(catalog), access=Everyone(), scheduler=reactor, timeout=None)
    await mount.send(delivered_to(fake_message()))

    followed = {describe(address) for address in mount.followed}

    # Both routes: the resource can be reloaded out of band, and it can be re-pended by the
    # cell its loader read. A reader depends on each of them.
    assert followed == {"Catalog(1).entries", "Catalog(1).key"}


async def test_an_out_of_band_reload_redraws_every_mount(bus: sl.runtime.TopicBus) -> None:
    reactor = Reactor(bus)
    catalog = Catalog(bus, 1)
    messages = [fake_message(message_id=1), fake_message(message_id=2)]
    mounts = [await mounted(catalog, reactor, message) for message in messages]

    await catalog.entries.reload()
    await drain(reactor, bus)

    assert [texts(message.edit.await_args.kwargs["view"]) for message in messages] == [["k1#2"], ["k1#2"]]
    assert all(mount.followed for mount in mounts)


async def test_a_write_to_a_cell_the_loader_read_reloads_once_for_everyone(bus: sl.runtime.TopicBus) -> None:
    reactor = Reactor(bus)
    catalog = Catalog(bus, 1)
    messages = [fake_message(message_id=1), fake_message(message_id=2)]
    mounts = [await mounted(catalog, reactor, message) for message in messages]

    with sl.runtime.transaction():
        catalog.key = "k2"
    await drain(reactor, bus)
    await drain(reactor, bus)

    assert catalog._loads == 2, "one reload served both mounts"
    assert all(mount.followed for mount in mounts)
    assert [texts(message.edit.await_args.kwargs["view"]) for message in messages] == [["k2#2"], ["k2#2"]]


async def test_a_replace_publishes_when_its_action_commits(bus: sl.runtime.TopicBus) -> None:
    reactor = Reactor(bus)
    catalog = Catalog(bus, 1)
    message = fake_message()
    mount = await mounted(catalog, reactor, message)

    with sl.runtime.transaction():
        catalog.entries.replace("installed")
    await drain(reactor, bus)

    assert texts(message.edit.await_args.kwargs["view"]) == ["installed"]
    assert mount.followed


async def test_a_rolled_back_replace_publishes_nothing(bus: sl.runtime.TopicBus) -> None:
    """Doc 48 staging, seen from the bus: an action that failed must not wake other mounts."""
    reactor = Reactor(bus)
    catalog = Catalog(bus, 1)
    message = fake_message()
    mount = await mounted(catalog, reactor, message)
    edits = message.edit.await_count

    with pytest.raises(RuntimeError), sl.runtime.transaction():
        catalog.entries.replace("installed")
        failure = "handler failed"
        raise RuntimeError(failure)
    await drain(reactor, bus)

    assert catalog.entries.value == "k1#1"
    assert message.edit.await_count == edits
    assert mount.followed


def test_two_namespaces_hold_separate_resources(bus: sl.runtime.TopicBus) -> None:
    one, two = Catalog(bus, 1), Catalog(bus, 2)

    assert one.entries is not two.entries
    assert one.entries.address != two.entries.address


def test_a_component_resource_carries_no_address() -> None:
    """Nothing else can be looking at it, so there is nothing to publish."""

    class Panel(sl.Component):
        @sl.resource
        async def value(self) -> str:
            return "loaded"

        def render(self):
            return sl.paragraph("x")

    assert Panel().value.address is None


# --- Declaration errors ---------------------------------------------------------------


def test_a_namespace_resource_may_not_take_a_reserved_name() -> None:
    with pytest.raises(TypeError, match="reserves 'scope'"):

        class Bad(sl.runtime.Shared[int]):
            @sl.resource
            async def scope(self) -> str:  # type: ignore[override]
                return "x"


def test_a_namespace_computed_may_not_take_a_reserved_name() -> None:
    with pytest.raises(TypeError, match="reserves 'bus'"):

        class Bad(sl.runtime.Shared[int]):
            @sl.computed
            def bus(self) -> str:  # type: ignore[override]
                return "x"
