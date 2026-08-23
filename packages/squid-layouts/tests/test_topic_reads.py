"""Mounts following the named topics their renders read, through `sl.runtime.watch`.

`test_shared_follow.py` covers the same reconciliation for `sl.runtime.Shared` cells. The difference
here is where the read happens: a topic is watched inside a `sl.resource` loader, under the
resource's consumer rather than the render's, so these tests are mostly about whether that
read still reaches the render that used the value.
"""

import asyncio
from typing import Any

import anyio
import discord

import squid_layouts as sl
from squid_layouts import Component, resource, state
from squid_layouts.discord import Everyone, Mount, Reactor
from squid_layouts.discord.delivery import DeliveryReceipt, handle_for
from squid_layouts.discord.testing import delivered_to, fake_message
from squid_layouts.primitives import Text
from squid_layouts.runtime import LocalTopicBus, PendingPolicy, Topic

BUILD = Topic("build", "1")
OTHER = Topic("build", "2")


class Watcher(Component):
    """One resource, one watched topic, chosen by state so a branch can drop it."""

    topic: Topic = state(default=BUILD)

    def __init__(self, load, *, topics: tuple[Topic, ...] | None = None) -> None:
        self._load = load
        self._topics = topics

    @resource(pending=PendingPolicy.ATOMIC)
    async def value(self) -> str:
        sl.runtime.watch(*(self._topics if self._topics is not None else (self.topic,)))
        return await self._load()

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


async def drain(reactor: Reactor, bus: LocalTopicBus) -> None:
    del bus
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reactor.run)
        await asyncio.wait_for(reactor._queue.join(), timeout=1)
        tasks.cancel_scope.cancel()


def texts(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


async def mounted(panel: Component, bus: LocalTopicBus, reactor: Reactor) -> tuple[Mount, Any]:
    message: Any = fake_message()
    mount = Mount(panel, access=Everyone(), scheduler=reactor, timeout=None)
    await mount.send(delivered_to(message))
    return mount, message


# --- The bridge -----------------------------------------------------------------------


async def test_a_topic_watched_only_inside_a_loader_is_followed_by_the_mount() -> None:
    """The whole phase rests on this: a loader's read has to reach the render's observations."""
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, _ = counting_loader(["first"])
    mount, message = await mounted(Watcher(load), bus, reactor)

    assert mount.followed == (BUILD,)
    assert mount.observed == (BUILD,)


async def test_a_variadic_watch_follows_every_topic_it_names() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, _ = counting_loader(["first"])
    mount, message = await mounted(Watcher(load, topics=(BUILD, OTHER)), bus, reactor)

    assert set(mount.followed) == {BUILD, OTHER}


async def test_a_topic_watched_in_render_is_followed_too() -> None:
    """`watch` is a tracked read, so the render's own consumer collects it directly."""

    class Direct(Component):
        def render(self):
            sl.runtime.watch(BUILD)
            return Text("x")

    bus = LocalTopicBus()
    reactor = Reactor(bus)
    mount, message = await mounted(Direct(), bus, reactor)

    assert mount.followed == (BUILD,)


# --- Refreshing -----------------------------------------------------------------------


async def test_one_publish_refreshes_a_watching_mount_exactly_once() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, _ = counting_loader(["first"])
    mount, message = await mounted(Watcher(load), bus, reactor)
    refreshes = 0

    async def refresh_now(*, links=()) -> None:
        nonlocal refreshes
        refreshes += 1

    mount.refresh_now = refresh_now  # pyrefly: ignore

    bus.publish(BUILD)
    await drain(reactor, bus)

    assert refreshes == 1


async def test_a_publish_reloads_the_resource_and_redraws_the_new_value() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, loads = counting_loader(["first", "second"])
    mount, message = await mounted(Watcher(load), bus, reactor)
    assert loads() == 1

    bus.publish(BUILD)
    await drain(reactor, bus)

    assert loads() == 2
    assert "ready:second" in texts(message.edit.await_args.kwargs["view"])


async def test_an_unrelated_publish_does_not_reload() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, loads = counting_loader(["first", "second"])
    await mounted(Watcher(load), bus, reactor)

    bus.publish(OTHER)
    await drain(reactor, bus)

    assert loads() == 1


async def test_a_mount_with_no_reactor_still_refetches_on_its_next_render() -> None:
    """The version moves on publish whether or not anything was subscribed."""
    bus = LocalTopicBus()
    load, loads = counting_loader(["first", "second"])
    panel = Watcher(load)
    mount = Mount(panel, access=Everyone(), timeout=None)
    await mount.send(delivered_to(fake_message()))
    assert loads() == 1

    bus.publish(BUILD)
    await mount.refresh_now()

    assert loads() == 2


# --- The race the old API only documented ---------------------------------------------


async def test_a_publish_during_the_load_is_not_lost() -> None:
    """`follow` told hosts to subscribe before the first read. A version needs no such rule."""
    bus = LocalTopicBus()
    reactor = Reactor(bus)
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

    message: Any = fake_message()
    sent: list[discord.ui.LayoutView] = []

    async def destination(presentation) -> Any:
        sent.append(presentation.layout)
        return DeliveryReceipt(message, handle_for(message))

    mount = Mount(Watcher(load), access=Everyone(), scheduler=reactor, timeout=None)
    await mount.send(destination)

    assert released.is_set()
    await drain(reactor, bus)

    # The stale value never reached Discord at all: an atomic resource re-settles before it
    # draws, so the publish is absorbed inside the send rather than costing a second edit.
    assert loads == 2
    assert texts(sent[-1]) == "ready:load2"
    message.edit.assert_not_awaited()


# --- Reconciliation, mirroring the shared-cell cases ----------------------------------


async def test_a_dropped_conditional_watch_stops_refreshing_once_delivered() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, _ = counting_loader(["first", "second"])
    panel = Watcher(load)
    mount, message = await mounted(panel, bus, reactor)
    assert mount.followed == (BUILD,)

    panel.topic = OTHER
    await mount.refresh_now()

    assert mount.followed == (OTHER,)
    assert {snapshot.topic for snapshot in bus.snapshot().topics} == {OTHER}


async def test_a_discarded_staged_render_leaves_no_permanent_follow() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, _ = counting_loader(["first", "second"])
    panel = Watcher(load)
    mount, message = await mounted(panel, bus, reactor)

    panel.topic = OTHER
    candidate = mount._stage()
    mount._rollback(candidate)

    panel.topic = BUILD
    await mount.refresh_now()
    assert mount.followed == (BUILD,)


async def test_no_follow_outlives_its_mount() -> None:
    bus = LocalTopicBus()
    reactor = Reactor(bus)
    load, _ = counting_loader(["first"])
    mount, message = await mounted(Watcher(load), bus, reactor)
    assert {snapshot.topic for snapshot in bus.snapshot().topics} == {BUILD}

    await mount.finish()

    assert not any(snapshot.subscribers for snapshot in bus.snapshot().topics)


# --- The cell itself ------------------------------------------------------------------


def test_a_topic_nobody_watches_holds_no_cell() -> None:
    """The registry is weak, so an unwatched topic costs nothing and needs no cleanup."""
    import gc

    from squid_reactive.topics import _TOPIC_CELLS

    lonely = Topic("lonely", "1")
    bus = LocalTopicBus()
    bus.publish(lonely)
    gc.collect()

    assert lonely not in _TOPIC_CELLS


def test_watching_a_topic_installs_no_commit_precondition() -> None:
    """A watch is not an observation: nothing writes a topic, so nothing can lose an update."""
    from squid_layouts.runtime.reactivity import _CURRENT, _Transaction

    with sl.runtime.transaction():
        sl.runtime.watch(BUILD)
        current = _CURRENT.get()
        assert isinstance(current, _Transaction)
        assert not current.observed


# --- Against the resource's own machinery ---------------------------------------------


async def test_a_publish_moves_a_resources_sources_so_it_repends() -> None:
    """The mechanism under the mount: a topic cell is an ordinary tracked source."""
    load, loads = counting_loader(["first", "second"])
    panel = Watcher(load)
    await panel.value.reload()
    assert isinstance(panel.value.status, sl.resources.Ready)

    LocalTopicBus().publish(BUILD)

    assert panel.value.pending
    assert loads() == 1, "re-pending is a pull: nothing reloads until someone settles it"


async def test_replace_rebaselines_against_a_topic_so_a_later_publish_still_reloads() -> None:
    """A local edit installs its own value without deafening the resource to the topic."""
    load, _ = counting_loader(["first", "second"])
    panel = Watcher(load)
    await panel.value.reload()
    bus = LocalTopicBus()

    bus.publish(BUILD)
    panel.value.replace("edited")
    assert panel.value.value == "edited", "replace supersedes the publish that preceded it"

    bus.publish(BUILD)
    assert panel.value.pending
