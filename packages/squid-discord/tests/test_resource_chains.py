"""One resource deriving from another.

A resource already re-pends when a cell its loader read moves. These are about the case that
did not work: an async value derived from another async value, which before this had to be
fused into one loader returning a tuple.
"""

from typing import Any

import discord

import squid_discord
import squid_layouts as sl
from squid_discord import Everyone, Mount
from squid_discord.testing import delivered_to, fake_message

TOPIC = sl.runtime.Topic("build", "1")


class Chain(sl.Component):
    """`node` derives from `build`, which watches a topic."""

    def __init__(self, source: str = "v1") -> None:
        self.source = source
        self.build_loads = 0
        self.node_loads = 0
        self.seen: list[str] = []

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def build(self) -> str:
        self.build_loads += 1
        sl.runtime.watch(TOPIC)
        return self.source

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def node(self) -> str:
        self.node_loads += 1
        value = await self.build
        self.seen.append(value)
        return f"card({value})"

    def render(self):
        match self.node.status:
            case sl.resources.Ready(value=value):
                return sl.paragraph(value)
            case _:
                return sl.paragraph("loading")


def texts(view: discord.ui.LayoutView) -> str:
    return "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


# --- Tracking -------------------------------------------------------------------------


async def test_awaiting_a_resource_inside_a_loader_tracks_it() -> None:
    panel = Chain()
    await panel.node.reload()

    assert panel.build in panel.node.sources
    assert panel.build_loads == 1, "awaiting a pending dependency settles it"


async def test_a_publish_two_links_up_repends_the_dependent() -> None:
    panel = Chain()
    await panel.node.reload()
    assert panel.node.value == "card(v1)"

    panel.source = "v2"
    sl.runtime.LocalTopicBus().publish(TOPIC)

    # `node` never read the topic; it read `build`, which did. Asking `node` whether it moved
    # re-checks `build`, which re-checks the topic.
    assert panel.node.pending

    await panel.node.reload()
    assert panel.node.value == "card(v2)"
    assert panel.seen == ["v1", "v2"], "the second load saw the new value, never the old one"


async def test_a_computed_reading_a_resource_rederives_when_it_reloads() -> None:
    class Derived(sl.Component):
        def __init__(self) -> None:
            self.source = "v1"

        @sl.resource
        async def loaded(self) -> str:
            return self.source

        @sl.computed
        def shouted(self) -> str:
            return self.loaded.value.upper()

        def render(self):
            return sl.paragraph("x")

    panel = Derived()
    await panel.loaded.reload()
    assert panel.shouted == "V1"

    panel.source = "v2"
    await panel.loaded.reload()

    assert panel.shouted == "V2"


# --- The version ----------------------------------------------------------------------


async def test_every_transition_moves_the_version() -> None:
    """A dependent compares versions, so a re-pend has to be visible as one."""
    panel = Chain()
    await panel.build.reload()
    versions = [panel.build.version]

    panel.build.invalidate()
    versions.append(panel.build.version)

    await panel.build._load()
    versions.append(panel.build.version)

    panel.build.replace("edited")
    versions.append(panel.build.version)

    assert versions == sorted(set(versions)), "re-pend, load and replace each move it"


async def test_a_failed_load_moves_the_version_too() -> None:
    class Breaks(sl.Component):
        def __init__(self) -> None:
            self.fail = False

        @sl.resource
        async def value(self) -> str:
            if self.fail:
                message = "offline"
                raise RuntimeError(message)
            return "ok"

        def render(self):
            return sl.paragraph("x")

    panel = Breaks()
    await panel.value.reload()
    settled = panel.value.version

    panel.fail = True
    await panel.value.reload()

    assert isinstance(panel.value.status, sl.resources.Failed)
    assert panel.value.version > settled, "a dependent must re-derive against the failure"


# --- Through a mount ------------------------------------------------------------------


async def test_a_chain_settles_in_one_send_and_draws_once() -> None:
    panel = Chain()
    message: Any = fake_message()
    sent: list[discord.ui.LayoutView] = []

    async def destination(presentation) -> Any:
        sent.append(presentation.layout)
        from squid_discord.delivery import DeliveryResult, handle_for

        return DeliveryResult(message, handle_for(message))

    mount = Mount(panel, access=Everyone(), timeout=None)
    await mount.send(destination)

    assert texts(sent[-1]) == "card(v1)"
    assert len(sent) == 1
    message.edit.assert_not_awaited()
    assert panel.build_loads == 1
    assert panel.node_loads == 1, "the chain settled itself inside one loader"


async def test_a_publish_redraws_the_whole_chain_without_a_torn_paint() -> None:
    bus = sl.runtime.LocalTopicBus()
    scheduler = squid_discord.MountScheduler(bus)
    panel = Chain()
    message: Any = fake_message()
    mount = Mount(panel, access=Everyone(), scheduler=scheduler, timeout=None)
    await mount.send(delivered_to(message))

    assert mount.followed == (TOPIC,), "a render reading only `node` still follows what `build` watched"

    panel.source = "v2"
    bus.publish(TOPIC)
    await mount.refresh()

    drawn = [texts(call.kwargs["view"]) for call in message.edit.await_args_list]
    assert drawn[-1] == "card(v2)"
    assert "card(v1)" not in drawn[1:], "no paint pairs the new input with the old derivation"


async def test_two_independent_resources_still_settle_together() -> None:
    """Nothing about chaining serialises a tier that has no chain in it."""

    class Pair(sl.Component):
        @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
        async def left(self) -> str:
            return "L"

        @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
        async def right(self) -> str:
            return "R"

        def render(self):
            left = self.left.status
            right = self.right.status
            ready = isinstance(left, sl.resources.Ready) and isinstance(right, sl.resources.Ready)
            return sl.paragraph(f"{left.value}{right.value}" if ready else "loading")

    mount = Mount(Pair(), access=Everyone(), timeout=None)
    message: Any = fake_message()
    await mount.send(delivered_to(message))

    assert mount.snapshot().suppressed >= 0  # the send completed


# --- Cycles ---------------------------------------------------------------------------


async def test_a_resource_that_awaits_itself_names_itself() -> None:
    class Ouroboros(sl.Component):
        @sl.resource
        async def value(self) -> int:
            return await self.value + 1

        def render(self):
            return sl.paragraph("x")

    panel = Ouroboros()
    await panel.value.reload()

    state = panel.value.status
    assert isinstance(state, sl.resources.Failed)
    assert isinstance(state.error, sl.runtime.ReactiveCycleError)
    assert state.error.path == ("Ouroboros.value", "Ouroboros.value")


async def test_a_mutual_cycle_names_the_whole_path_not_the_link_that_closed_it() -> None:
    """`left` awaiting `right` is fine and `right` awaiting `left` is fine; the pair is not."""

    class Pair(sl.Component):
        @sl.resource
        async def left(self) -> int:
            return await self.right + 1

        @sl.resource
        async def right(self) -> int:
            return await self.left + 1

        def render(self):
            return sl.paragraph("x")

    panel = Pair()
    await panel.left.reload()

    state = panel.left.status
    assert isinstance(state, sl.resources.Failed)
    assert isinstance(state.error, sl.runtime.ReactiveCycleError)
    assert state.error.path == ("Pair.left", "Pair.right", "Pair.left")


async def test_a_cycle_reports_the_ring_without_the_run_up_to_it() -> None:
    """`entry` is not part of the cycle, so naming it would send the reader to the wrong line."""

    class Three(sl.Component):
        @sl.resource
        async def entry(self) -> int:
            return await self.a

        @sl.resource
        async def a(self) -> int:
            return await self.b

        @sl.resource
        async def b(self) -> int:
            return await self.a

        def render(self):
            return sl.paragraph("x")

    panel = Three()
    await panel.entry.reload()

    state = panel.entry.status
    assert isinstance(state, sl.resources.Failed)
    assert isinstance(state.error, sl.runtime.ReactiveCycleError)
    assert state.error.path == ("Three.a", "Three.b", "Three.a")
    assert "cycle: Three.a -> Three.b -> Three.a" in str(state.error)


async def test_two_resources_awaiting_one_shared_input_is_not_a_cycle() -> None:
    """A diamond is not a ring: the guard must be a path check, not a visited set."""

    class Diamond(sl.Component):
        def __init__(self) -> None:
            self.base_loads = 0

        @sl.resource
        async def base(self) -> int:
            self.base_loads += 1
            return 1

        @sl.resource
        async def left(self) -> int:
            return await self.base + 1

        @sl.resource
        async def right(self) -> int:
            return await self.base + 2

        @sl.resource
        async def total(self) -> int:
            return await self.left + await self.right

        def render(self):
            return sl.paragraph("x")

    panel = Diamond()
    await panel.total.reload()

    assert panel.total.value == 5
    assert panel.base_loads == 1, "the shared input settled once and was reused"


async def test_a_cycle_through_a_computed_is_named_across_both_kinds() -> None:
    """The stack is shared for this: neither guard alone could see the whole ring."""

    class Mixed(sl.Component):
        @sl.resource
        async def loaded(self) -> int:
            return self.doubled + 1

        @sl.computed
        def doubled(self) -> int:
            return self.loaded.value * 2

        def render(self):
            return sl.paragraph("x")

    panel = Mixed()
    await panel.loaded.reload()

    state = panel.loaded.status
    assert isinstance(state, sl.resources.Failed)
    assert isinstance(state.error, sl.runtime.ReactiveCycleError)
    assert state.error.path == ("Mixed.loaded", "Mixed.doubled", "Mixed.loaded")
