"""Computed values and resources declared on an `sl.runtime.SharedState` namespace.

A cell on a namespace is state several mounts share. These are the two derived forms of the
same idea: a computed, which is a pure function of cells and needs no address of its own, and
a resource, which is loaded and therefore can move on its own -- so it publishes.
"""

import pytest

import squid_ui as sl
from squid_ui.runtime.shared import describe
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler
from squid_ui_discord import testing as sd
from squid_ui_discord.testing import delivered_to, fake_message


class Prefs(sl.runtime.SharedState[int]):
    first: str = sl.state("Ada")
    last: str = sl.state("Lovelace")
    unread: str = sl.state("not looked at")

    @sl.computed
    def full(self) -> str:
        return f"{self.first} {self.last}"


class Catalog(sl.runtime.SharedState[int]):
    key: str = sl.state("k1")

    def __init__(self, bus: sl.runtime.LocalTopicBus, scope: int) -> None:
        super().__init__(bus, scope)
        self._loads = 0

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def entries(self) -> str:
        self._loads += 1
        return f"{self.key}#{self._loads}"


class Reader(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def render(self):
        match self.catalog.entries.status:
            case sl.resources.Ready(value=value):
                return sl.paragraph(value)
            case _:
                return sl.paragraph("loading")


async def mounted(catalog: Catalog, scheduler: MessageRootScheduler, message: object) -> MessageRoot:
    """Send a reader, and hand the mount back so the caller keeps it alive.

    A scheduler holds its mounts weakly, so a test that drops the reference is testing whether
    the collector ran rather than whether the refresh works.
    """
    message_root = MessageRoot(Reader(catalog), access=Everyone(), scheduler=scheduler, timeout=None)
    await message_root.send(delivered_to(message))
    return message_root


@pytest.fixture
def bus() -> sl.runtime.LocalTopicBus:
    return sl.runtime.LocalTopicBus()


# --- Computed on a namespace ----------------------------------------------------------


def test_a_namespace_computed_derives_from_its_own_cells(bus: sl.runtime.LocalTopicBus) -> None:
    prefs = Prefs(bus, 1)
    assert prefs.full == "Ada Lovelace"

    prefs.first = "Grace"

    assert prefs.full == "Grace Lovelace"


def test_two_namespaces_compute_independently(bus: sl.runtime.LocalTopicBus) -> None:
    """The computed is per-instance, like the cells it reads: the handle is the state."""
    one, two = Prefs(bus, 1), Prefs(bus, 2)

    one.first = "Grace"

    assert (one.full, two.full) == ("Grace Lovelace", "Ada Lovelace")


async def test_a_message_root_reading_a_namespace_computed_follows_the_cells_behind_it(
    bus: sl.runtime.LocalTopicBus,
) -> None:
    """A computed carries no address: what moves is the cells, so those are what to follow."""
    scheduler = MessageRootScheduler(bus)
    prefs = Prefs(bus, 1)

    class Panel(sl.Component[sl.ComponentsV2Target]):
        def render(self):
            return sl.paragraph(prefs.full)

    message = fake_message()
    message_root = MessageRoot(Panel(), access=Everyone(), scheduler=scheduler, timeout=None)
    await message_root.send(delivered_to(message))

    assert {describe(address) for address in message_root.followed} == {"Prefs(1).first", "Prefs(1).last"}

    with sl.runtime.transaction():
        prefs.first = "Grace"
    await sd.drain(scheduler)
    await message_root.refresh()

    assert sd.payload_texts(message.edit.await_args.kwargs["view"]) == ["Grace Lovelace"]


# --- Resource on a namespace ----------------------------------------------------------


async def test_one_namespace_resource_loads_once_for_every_message_root_holding_it(
    bus: sl.runtime.LocalTopicBus,
) -> None:
    scheduler = MessageRootScheduler(bus)
    catalog = Catalog(bus, 1)

    message_roots = [await mounted(catalog, scheduler, fake_message(message_id=message_id)) for message_id in (1, 2)]

    assert catalog._loads == 1, "the second mount shared the value rather than loading its own"
    assert len(message_roots) == 2


async def test_a_namespace_resource_is_followed_by_its_own_address(bus: sl.runtime.LocalTopicBus) -> None:
    scheduler = MessageRootScheduler(bus)
    catalog = Catalog(bus, 1)
    message_root = MessageRoot(Reader(catalog), access=Everyone(), scheduler=scheduler, timeout=None)
    await message_root.send(delivered_to(fake_message()))

    followed = {describe(address) for address in message_root.followed}

    # Both routes: the resource can be reloaded out of band, and it can be re-pended by the
    # cell its loader read. A reader depends on each of them.
    assert followed == {"Catalog(1).entries", "Catalog(1).key"}


async def test_an_out_of_band_reload_redraws_every_root(bus: sl.runtime.LocalTopicBus) -> None:
    scheduler = MessageRootScheduler(bus)
    catalog = Catalog(bus, 1)
    messages = [fake_message(message_id=1), fake_message(message_id=2)]
    message_roots = [await mounted(catalog, scheduler, message) for message in messages]

    await catalog.entries.reload()
    await sd.drain(scheduler)

    assert [sd.payload_texts(message.edit.await_args.kwargs["view"]) for message in messages] == [["k1#2"], ["k1#2"]]
    assert all(message_root.followed for message_root in message_roots)


async def test_a_write_to_a_cell_the_loader_read_reloads_once_for_everyone(bus: sl.runtime.LocalTopicBus) -> None:
    scheduler = MessageRootScheduler(bus)
    catalog = Catalog(bus, 1)
    messages = [fake_message(message_id=1), fake_message(message_id=2)]
    message_roots = [await mounted(catalog, scheduler, message) for message in messages]

    with sl.runtime.transaction():
        catalog.key = "k2"
    await sd.drain(scheduler)
    await sd.drain(scheduler)

    assert catalog._loads == 2, "one reload served both mounts"
    assert all(message_root.followed for message_root in message_roots)
    assert [sd.payload_texts(message.edit.await_args.kwargs["view"]) for message in messages] == [["k2#2"], ["k2#2"]]


async def test_a_replace_publishes_when_its_action_commits(bus: sl.runtime.LocalTopicBus) -> None:
    scheduler = MessageRootScheduler(bus)
    catalog = Catalog(bus, 1)
    message = fake_message()
    message_root = await mounted(catalog, scheduler, message)

    with sl.runtime.transaction():
        catalog.entries.replace("installed")
    await sd.drain(scheduler)

    assert sd.payload_texts(message.edit.await_args.kwargs["view"]) == ["installed"]
    assert message_root.followed


async def test_a_rolled_back_replace_publishes_nothing(bus: sl.runtime.LocalTopicBus) -> None:
    """Doc 48 staging, seen from the bus: an action that failed must not wake other mounts."""
    scheduler = MessageRootScheduler(bus)
    catalog = Catalog(bus, 1)
    message = fake_message()
    message_root = await mounted(catalog, scheduler, message)
    edits = message.edit.await_count

    with pytest.raises(RuntimeError), sl.runtime.transaction():
        catalog.entries.replace("installed")
        failure = "handler failed"
        raise RuntimeError(failure)
    await sd.drain(scheduler)

    assert catalog.entries.value == "k1#1"
    assert message.edit.await_count == edits
    assert message_root.followed


def test_two_namespaces_hold_separate_resources(bus: sl.runtime.LocalTopicBus) -> None:
    one, two = Catalog(bus, 1), Catalog(bus, 2)

    assert one.entries is not two.entries
    assert one.entries.address != two.entries.address


def test_a_component_resource_carries_no_address() -> None:
    """Nothing else can be looking at it, so there is nothing to publish."""

    class Panel(sl.Component[sl.ComponentsV2Target]):
        @sl.resource
        async def value(self) -> str:
            return "loaded"

        def render(self):
            return sl.paragraph("x")

    assert Panel().value.address is None


# --- Declaration errors ---------------------------------------------------------------


def test_a_namespace_resource_may_not_take_a_reserved_name() -> None:
    with pytest.raises(TypeError, match="reserves 'scope'"):

        class Bad(sl.runtime.SharedState[int]):
            @sl.resource
            async def scope(self) -> str:  # type: ignore[override]
                return "x"


def test_a_namespace_computed_may_not_take_a_reserved_name() -> None:
    with pytest.raises(TypeError, match="reserves 'bus'"):

        class Bad(sl.runtime.SharedState[int]):
            @sl.computed
            def bus(self) -> str:  # type: ignore[override]
                return "x"
