"""Message roots refresh when a topic used by their content changes.

A resource loader declares its dependency with `sl.runtime.watch(topic)`. When a component
renders that resource, the message root must also follow the topic so a publication can
reload the value and refresh the message. These tests cover that indirect dependency as
well as topics watched directly during render. `test_shared_follow.py` covers the same
subscription updates for `sl.Reactive` cells.
"""

import asyncio
import gc
import weakref
from typing import Any, override

import discord

import squid_ui as sl
from squid_ui import Component, resource, state
from squid_ui.primitives import Text
from squid_ui.runtime import CellAddress, LocalTopicBus, PendingMode, Topic, TopicHandle
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler
from squid_ui_discord import testing as sd
from squid_ui_discord.delivery import DeliveryResult, handle_for
from squid_ui_discord.testing import delivered_to, message_harness

BUILD = Topic("build", "1")
OTHER = Topic("build", "2")


class Watcher(Component[sl.ComponentsV2Target]):
    """One resource, one watched topic, chosen by state so a branch can drop it."""

    topic: Topic = state(default=BUILD)

    def __init__(self, bus: LocalTopicBus, load, *, topics: tuple[Topic, ...] | None = None) -> None:
        self._bus = bus
        self._load = load
        self._topics = topics

    @resource(pending=PendingMode.ATOMIC)
    async def value(self) -> str:
        for name in self._topics if self._topics is not None else (self.topic,):
            self._bus.topic(name.kind, name.key).watch()
        return await self._load()

    @override
    def render(self):
        match self.value.status:
            case sl.resources.Ready(value=value):
                return Text(f"ready:{value}")
            case _:
                return Text("pending")


def counting_loader(values: list[str]):
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        return values[min(loads - 1, len(values) - 1)]

    return load, lambda: loads


def texts(view: discord.ui.LayoutView) -> str:
    """Join the rendered text fragments for whole-message assertions."""
    return "\n".join(sd.payload_texts(view))


async def mounted(
    panel: Component[Any, Any], bus: LocalTopicBus, scheduler: MessageRootScheduler
) -> tuple[MessageRoot, Any]:
    message: Any = message_harness()
    message_root = MessageRoot(panel, access=Everyone(), scheduler=scheduler, timeout=None)
    await message_root.send(delivered_to(message))
    return message_root, message


# --- The bridge -----------------------------------------------------------------------


async def test_a_topic_watched_only_inside_a_loader_is_followed_by_the_root() -> None:
    """A loader's tracked topic read must reach the render that consumes its result."""
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, _ = counting_loader(["first"])
    message_root, message = await mounted(Watcher(bus, load), bus, scheduler)

    assert tuple(address for address in message_root.followed if isinstance(address, TopicHandle)) == (
        bus.topic(BUILD.kind, BUILD.key),
    )
    assert message_root.observed == (
        CellAddress(message_root.component, "value"),
        CellAddress(message_root.component, "topic"),
        bus.topic(BUILD.kind, BUILD.key),
    )


async def test_a_variadic_watch_follows_every_topic_it_names() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, _ = counting_loader(["first"])
    message_root, message = await mounted(Watcher(bus, load, topics=(BUILD, OTHER)), bus, scheduler)

    assert set(message_root.followed) == {
        CellAddress(message_root.component, "value"),
        bus.topic(BUILD.kind, BUILD.key),
        bus.topic(OTHER.kind, OTHER.key),
    }


async def test_a_topic_watched_in_render_is_followed_too() -> None:
    """`watch` is a tracked read, so the render's own consumer collects it directly."""

    class Direct(Component[sl.ComponentsV2Target]):
        @override
        def render(self):
            bus.topic(BUILD.kind, BUILD.key).watch()
            return Text("x")

    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    message_root, message = await mounted(Direct(), bus, scheduler)

    assert tuple(address for address in message_root.followed if isinstance(address, TopicHandle)) == (
        bus.topic(BUILD.kind, BUILD.key),
    )


# --- Refreshing -----------------------------------------------------------------------


async def test_one_publish_refreshes_a_watching_message_root_exactly_once() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, _ = counting_loader(["first"])
    message_root, message = await mounted(Watcher(bus, load), bus, scheduler)
    refreshes = 0

    async def refresh(*, links=()) -> None:
        nonlocal refreshes
        refreshes += 1

    message_root.refresh = refresh  # pyright: ignore[reportAttributeAccessIssue]  # a stub for the one `refresh(links=...)` call the scheduler makes  # pyrefly: ignore

    bus.publish(BUILD)
    await sd.drain(scheduler)

    assert refreshes == 1


async def test_a_publish_reloads_the_resource_and_redraws_the_new_value() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, loads = counting_loader(["first", "second"])
    message_root, message = await mounted(Watcher(bus, load), bus, scheduler)
    assert loads() == 1

    bus.publish(BUILD)
    await sd.drain(scheduler)

    assert loads() == 2
    assert "ready:second" in texts(message.edit.await_args.kwargs["view"])


async def test_an_unrelated_publish_does_not_reload() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, loads = counting_loader(["first", "second"])
    await mounted(Watcher(bus, load), bus, scheduler)

    bus.publish(OTHER)
    await sd.drain(scheduler)

    assert loads() == 1


async def test_a_message_root_with_no_reactor_still_refetches_on_its_next_render() -> None:
    """The version moves on publish whether or not anything was subscribed."""
    bus = LocalTopicBus()
    load, loads = counting_loader(["first", "second"])
    panel = Watcher(bus, load)
    message_root = MessageRoot(panel, access=Everyone(), timeout=None)
    await message_root.send(delivered_to(message_harness()))
    assert loads() == 1

    bus.publish(BUILD)
    await message_root.refresh()

    assert loads() == 2


# --- Publication before the first subscription -----------------------------------------


async def test_a_publish_during_the_load_is_not_lost() -> None:
    """A publish before subscription must invalidate the first load through its topic version."""
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    released = asyncio.Event()
    loads = 0

    async def load() -> str:
        nonlocal loads
        loads += 1
        if loads == 1:
            # Publish from inside the very first load, before its value is installed.
            bus.publish(BUILD)
            released.set()
        return f"load{loads}"

    message: Any = message_harness()
    sent: list[discord.ui.LayoutView] = []

    async def destination(presentation) -> Any:
        sent.append(presentation.layout)
        return DeliveryResult(message, handle_for(message))

    message_root = MessageRoot(Watcher(bus, load), access=Everyone(), scheduler=scheduler, timeout=None)
    await message_root.send(destination)

    assert released.is_set()
    await sd.drain(scheduler)

    # The stale value never reached Discord at all: an atomic resource re-settles before it
    # draws, so the publish is absorbed inside the send rather than costing a second edit.
    assert loads == 2
    assert texts(sent[-1]) == "ready:load2"
    message.edit.assert_not_awaited()


# --- Reconciliation, mirroring the shared-cell cases ----------------------------------


async def test_a_dropped_conditional_watch_stops_refreshing_once_delivered() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, _ = counting_loader(["first", "second"])
    panel = Watcher(bus, load)
    message_root, message = await mounted(panel, bus, scheduler)
    assert tuple(address for address in message_root.followed if isinstance(address, TopicHandle)) == (
        bus.topic(BUILD.kind, BUILD.key),
    )

    panel.topic = OTHER
    await message_root.refresh()

    assert tuple(address for address in message_root.followed if isinstance(address, TopicHandle)) == (
        bus.topic(OTHER.kind, OTHER.key),
    )
    assert {snapshot.topic for snapshot in bus.snapshot().topics} == {OTHER}


async def test_a_discarded_staged_render_leaves_no_permanent_follow() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, _ = counting_loader(["first", "second"])
    panel = Watcher(bus, load)
    message_root, message = await mounted(panel, bus, scheduler)

    panel.topic = OTHER
    candidate = message_root._stage()
    message_root._rollback(candidate)

    panel.topic = BUILD
    await message_root.refresh()
    assert tuple(address for address in message_root.followed if isinstance(address, TopicHandle)) == (
        bus.topic(BUILD.kind, BUILD.key),
    )


async def test_no_follow_outlives_its_root() -> None:
    bus = LocalTopicBus()
    scheduler = MessageRootScheduler(bus)
    load, _ = counting_loader(["first"])
    message_root, message = await mounted(Watcher(bus, load), bus, scheduler)
    assert {snapshot.topic for snapshot in bus.snapshot().topics} == {BUILD}

    await message_root.finish()

    assert not any(snapshot.subscribers for snapshot in bus.snapshot().topics)


# --- The cell itself ------------------------------------------------------------------


def test_a_topic_nobody_watches_holds_no_cell() -> None:
    """The weak registry releases a topic cell once no resource or render retains it."""
    bus = LocalTopicBus()
    handle = bus.topic("lonely", "1")
    reference = weakref.ref(handle)
    del handle
    gc.collect()
    assert reference() is None
    assert not bus._topics
    bus.publish(Topic("unwatched", "1"))
    assert not bus._topics


def test_watching_a_topic_installs_no_commit_precondition() -> None:
    """Watching a topic tracks invalidation without adding an optimistic transaction precondition."""
    from squid_reactivity.internals import CURRENT_TRANSACTION, Transaction

    bus = LocalTopicBus()
    with sl.runtime.transaction():
        bus.topic(BUILD.kind, BUILD.key).watch()
        current = CURRENT_TRANSACTION.get()
        assert isinstance(current, Transaction)
        assert not current.observed


# --- Against the resource's own machinery ---------------------------------------------


async def test_a_publish_moves_a_resources_sources_so_it_repends() -> None:
    """A resource tracks a topic cell just as it tracks other reactive dependencies."""
    bus = LocalTopicBus()
    load, loads = counting_loader(["first", "second"])
    panel = Watcher(bus, load)
    await panel.value.reload()
    assert isinstance(panel.value.status, sl.resources.Ready)

    bus.publish(BUILD)

    assert panel.value.pending
    assert loads() == 1, "re-pending is a pull: nothing reloads until someone settles it"


async def test_replace_rebaselines_against_a_topic_so_a_later_publish_still_reloads() -> None:
    """A local edit installs its own value without deafening the resource to the topic."""
    bus = LocalTopicBus()
    load, _ = counting_loader(["first", "second"])
    panel = Watcher(bus, load)
    await panel.value.reload()

    bus.publish(BUILD)
    panel.value.replace("edited")
    assert panel.value.value == "edited", "replace supersedes the publish that preceded it"

    bus.publish(BUILD)
    assert panel.value.pending


async def test_handle_delivery_is_independent_of_the_schedulers_bus() -> None:
    owner, unrelated = LocalTopicBus(), LocalTopicBus()
    scheduler = MessageRootScheduler()
    load, loads = counting_loader(["first", "second"])
    root, message = await mounted(Watcher(owner, load), owner, scheduler)
    unrelated.publish(BUILD)
    await sd.drain(scheduler)
    assert loads() == 1

    owner.topic(BUILD.kind, BUILD.key).publish()
    await sd.drain(scheduler)
    assert loads() == 2
    assert "ready:second" in texts(message.edit.await_args.kwargs["view"])
    await root.finish()
