"""Cursor position and async window-source contracts."""

import asyncio
from dataclasses import dataclass

import anyio

from squid_layouts import Position, Window, WindowCursor


@dataclass(frozen=True, slots=True)
class Entry:
    key: str


class ListSource:
    countable = True
    bidirectional = True
    jumpable = True

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        self.requests: list[Position] = []

    async def fetch(self, position: Position, extent: int) -> Window[Entry]:
        self.requests.append(position)
        if position.anchor in self.keys:
            anchor = self.keys.index(position.anchor)
            if position.direction == "forward":
                offset = anchor + 1
            elif position.direction == "backward":
                offset = max(0, anchor - extent)
            else:
                offset = anchor
        else:
            offset = min(position.offset, max(0, len(self.keys) - 1))
        items = tuple(Entry(key) for key in self.keys[offset : offset + extent])
        return Window(items, offset > 0, offset + extent < len(self.keys), len(self.keys), Position(offset=offset))


async def test_cursor_navigates_by_boundaries_and_keeps_offsets() -> None:
    source = ListSource(("a", "b", "c", "d", "e"))
    cursor = WindowCursor(source, 2, lambda entry: entry.key)

    assert await cursor.fetch()
    assert cursor.window is not None and cursor.window.items == (Entry("a"), Entry("b"))
    assert cursor.position == Position("a", 0)

    assert await cursor.next()
    assert source.requests[-1] == Position("b", 2, "forward")
    assert cursor.window.items == (Entry("c"), Entry("d"))
    assert cursor.position == Position("c", 2)

    assert await cursor.previous()
    assert source.requests[-1] == Position("c", 0, "backward")
    assert cursor.window.items == (Entry("a"), Entry("b"))


async def test_refresh_is_window_scoped_and_source_owns_anchor_gone_fallback() -> None:
    source = ListSource(("a", "b", "c"))
    cursor = WindowCursor(source, 2, lambda entry: entry.key)
    await cursor.fetch()
    previous = cursor.fingerprint

    source.keys = ("b", "c")
    assert await cursor.refresh()

    assert source.requests[-1] == Position("a", 0)
    assert cursor.window is not None and cursor.window.items == (Entry("b"), Entry("c"))
    assert cursor.position == Position("b", 0)
    assert cursor.fingerprint != previous


class RacingSource:
    countable = False
    bidirectional = True
    jumpable = False

    def __init__(self) -> None:
        self.started = {"old": asyncio.Event(), "new": asyncio.Event()}
        self.release = {"old": asyncio.Event(), "new": asyncio.Event()}

    async def fetch(self, position: Position, extent: int) -> Window[str]:
        del extent
        assert position.anchor is not None
        self.started[position.anchor].set()
        await self.release[position.anchor].wait()
        return Window((position.anchor,), has_prev=True, has_next=True)


async def test_out_of_order_fetch_result_is_dropped() -> None:
    source = RacingSource()
    cursor = WindowCursor(source, 1, lambda item: item)
    results: dict[str, bool] = {}
    finished = {"old": asyncio.Event(), "new": asyncio.Event()}

    async def fetch(name: str, position: Position) -> None:
        results[name] = await cursor.fetch(position)
        finished[name].set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(fetch, "old", Position("old", 1, "forward"))
        await source.started["old"].wait()
        tasks.start_soon(fetch, "new", Position("new", 2, "forward"))
        await source.started["new"].wait()
        source.release["new"].set()
        await finished["new"].wait()
        source.release["old"].set()

    assert results == {"new": True, "old": False}

    assert cursor.window is not None and cursor.window.items == ("new",)
    assert cursor.position == Position("new", 2)


async def test_forward_only_source_refuses_previous_without_fetching() -> None:
    source = ListSource(("a", "b", "c"))
    source.bidirectional = False
    cursor = WindowCursor(source, 1, lambda entry: entry.key, initial=Position(offset=1))
    await cursor.fetch()
    requests = len(source.requests)

    assert not await cursor.previous()
    assert len(source.requests) == requests
