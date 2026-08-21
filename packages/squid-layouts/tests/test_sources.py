"""Position policy and asynchronous window loading contracts."""

import asyncio
from dataclasses import dataclass

import anyio
import pytest

from squid_layouts import (
    CountPrecision,
    Direction,
    Position,
    SourceCapabilities,
    Window,
    WindowLoader,
)


@dataclass(frozen=True, slots=True)
class Entry:
    key: str


class ListSource:
    capabilities = SourceCapabilities(backward=True, offsets=True, jumpable=True, count=CountPrecision.EXACT)

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        self.requests: list[Position] = []

    async def fetch(self, position: Position, extent: int) -> Window[Entry]:
        self.requests.append(position)
        if position.anchor in self.keys:
            anchor = self.keys.index(position.anchor)
            if position.direction is Direction.FORWARD:
                offset = anchor + 1
            elif position.direction is Direction.BACKWARD:
                offset = max(0, anchor - extent)
            else:
                offset = anchor
        else:
            offset = min(position.offset, max(0, len(self.keys) - 1))
        items = tuple(Entry(key) for key in self.keys[offset : offset + extent])
        anchor = items[0].key if items else None
        return Window(
            Position(anchor, offset),
            items,
            has_previous=offset > 0,
            has_next=offset + extent < len(self.keys),
            total=len(self.keys),
        )


async def test_loader_navigates_by_boundaries_and_accepts_resolved_positions() -> None:
    source = ListSource(("a", "b", "c", "d", "e"))
    loader = WindowLoader(source, 2, lambda entry: entry.key)

    first = await loader.load()
    assert first is not None
    assert first.window.items == (Entry("a"), Entry("b"))
    assert first.position == Position("a", 0)

    second = await loader.next(first)
    assert second is not None
    assert source.requests[-1] == Position("b", 2, Direction.FORWARD)
    assert second.window.items == (Entry("c"), Entry("d"))
    assert second.position == Position("c", 2)

    returned = await loader.previous(second)
    assert returned is not None
    assert source.requests[-1] == Position("c", 0, Direction.BACKWARD)
    assert returned.window.items == (Entry("a"), Entry("b"))


async def test_refresh_delegates_anchor_gone_fallback_to_the_source() -> None:
    source = ListSource(("a", "b", "c"))
    loader = WindowLoader(source, 2, lambda entry: entry.key)
    first = await loader.load()
    assert first is not None

    source.keys = ("b", "c")
    refreshed = await loader.load(previous=first)

    assert refreshed is not None
    assert source.requests[-1] == Position("a", 0)
    assert refreshed.window.items == (Entry("b"), Entry("c"))
    assert refreshed.position == Position("b", 0)
    assert refreshed.fingerprint != first.fingerprint


class RacingSource:
    capabilities = SourceCapabilities(backward=True)

    def __init__(self) -> None:
        self.started = {"old": asyncio.Event(), "new": asyncio.Event()}
        self.release = {"old": asyncio.Event(), "new": asyncio.Event()}

    async def fetch(self, position: Position, extent: int) -> Window[str]:
        del extent
        assert position.anchor is not None
        self.started[position.anchor].set()
        await self.release[position.anchor].wait()
        return Window(Position(position.anchor), (position.anchor,), has_previous=True, has_next=True)


async def test_out_of_order_result_cannot_publish_state() -> None:
    source = RacingSource()
    loader = WindowLoader(source, 1, lambda item: item)
    results = {}
    finished = {"old": asyncio.Event(), "new": asyncio.Event()}

    async def fetch(name: str, position: Position) -> None:
        results[name] = await loader.load(position)
        finished[name].set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(fetch, "old", Position("old", 1, Direction.FORWARD))
        await source.started["old"].wait()
        tasks.start_soon(fetch, "new", Position("new", 2, Direction.FORWARD))
        await source.started["new"].wait()
        source.release["new"].set()
        await finished["new"].wait()
        source.release["old"].set()

    assert results["old"] is None
    assert results["new"] is not None and results["new"].window.items == ("new",)


@pytest.mark.parametrize(
    "capabilities",
    [
        SourceCapabilities(offsets=False, jumpable=False),
        SourceCapabilities(offsets=True, count=CountPrecision.APPROXIMATE),
        SourceCapabilities(offsets=True, jumpable=True, count=CountPrecision.EXACT),
    ],
)
def test_valid_capability_shapes(capabilities: SourceCapabilities) -> None:
    assert capabilities


def test_capabilities_reject_claims_that_require_unknown_offsets() -> None:
    with pytest.raises(ValueError, match="jumpable"):
        SourceCapabilities(jumpable=True)
    with pytest.raises(ValueError, match="countable"):
        SourceCapabilities(count=CountPrecision.EXACT)


def test_window_requires_a_resolved_position() -> None:
    with pytest.raises(ValueError, match="AROUND"):
        Window(Position("a", direction=Direction.FORWARD), ("a",), has_previous=False, has_next=False)
    with pytest.raises(ValueError, match="negative offset"):
        Window(Position("a", offset=-1), ("a",), has_previous=False, has_next=False)


async def test_loader_rejects_results_that_contradict_capabilities() -> None:
    source = ListSource(("a", "b"))
    source.capabilities = SourceCapabilities()
    with pytest.raises(ValueError, match="uncountable source returned a total"):
        await WindowLoader(source, 2, lambda entry: entry.key).load()

    class InvalidExactSource:
        capabilities = SourceCapabilities(offsets=True, count=CountPrecision.EXACT)

        async def fetch(self, position: Position, extent: int) -> Window[Entry]:
            del position, extent
            return Window(Position(offset=1), (Entry("a"),), has_previous=False, has_next=False, total=1)

    with pytest.raises(ValueError, match="beyond its total"):
        await WindowLoader(InvalidExactSource(), 2, lambda entry: entry.key).load(Position(offset=1))
