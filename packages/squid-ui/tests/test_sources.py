"""Position policy and asynchronous batch loading contracts."""

import asyncio
from dataclasses import dataclass

import anyio
import pytest

from squid_ui.sources import (
    Batch,
    BatchLoader,
    CountPrecision,
    Direction,
    Position,
    SourceCapabilities,
    list_source,
)


@dataclass(frozen=True, slots=True)
class Entry:
    key: str


class ListSource:
    """Resolve stable anchors against mutable fixture keys, falling back to offsets when an anchor disappears."""

    capabilities = SourceCapabilities(backward=True, offsets=True, jumpable=True, count=CountPrecision.EXACT)

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        self.requests: list[Position] = []

    async def fetch(self, position: Position, fetch_size: int) -> Batch[Entry]:
        self.requests.append(position)
        if position.anchor in self.keys:
            anchor = self.keys.index(position.anchor)
            if position.direction is Direction.FORWARD:
                offset = anchor + 1
            elif position.direction is Direction.BACKWARD:
                offset = max(0, anchor - fetch_size)
            else:
                offset = anchor
        else:
            offset = min(position.offset, max(0, len(self.keys) - 1))
        items = tuple(Entry(key) for key in self.keys[offset : offset + fetch_size])
        anchor = items[0].key if items else None
        return Batch(
            Position(anchor, offset),
            items,
            previous=Position(anchor, max(0, offset - fetch_size), Direction.BACKWARD) if offset > 0 else None,
            next=Position(items[-1].key, offset + len(items), Direction.FORWARD)
            if offset + fetch_size < len(self.keys)
            else None,
            item_count=len(self.keys),
        )


async def test_loader_navigates_by_boundaries_and_accepts_resolved_positions() -> None:
    source = ListSource(("a", "b", "c", "d", "e"))
    loader = BatchLoader(source, 2, lambda entry: entry.key)

    first = await loader.load()
    assert first is not None
    assert first.batch.items == (Entry("a"), Entry("b"))
    assert first.position == Position("a", 0)

    second = await loader.next(first)
    assert second is not None
    assert source.requests[-1] == Position("b", 2, Direction.FORWARD)
    assert second.batch.items == (Entry("c"), Entry("d"))
    assert second.position == Position("c", 2)

    returned = await loader.previous(second)
    assert returned is not None
    assert source.requests[-1] == Position("c", 0, Direction.BACKWARD)
    assert returned.batch.items == (Entry("a"), Entry("b"))


async def test_refresh_delegates_anchor_gone_fallback_to_the_source() -> None:
    source = ListSource(("a", "b", "c"))
    loader = BatchLoader(source, 2, lambda entry: entry.key)
    first = await loader.load()
    assert first is not None

    source.keys = ("b", "c")
    refreshed = await loader.load(previous=first)

    assert refreshed is not None
    assert source.requests[-1] == Position("a", 0)
    assert refreshed.batch.items == (Entry("b"), Entry("c"))
    assert refreshed.position == Position("b", 0)
    assert refreshed.fingerprint != first.fingerprint


class RacingSource:
    """Hold each fetch behind its own event so the test controls completion order without sleeps."""

    capabilities = SourceCapabilities(backward=True)

    def __init__(self) -> None:
        self.started = {"old": asyncio.Event(), "new": asyncio.Event()}
        self.release = {"old": asyncio.Event(), "new": asyncio.Event()}

    async def fetch(self, position: Position, fetch_size: int) -> Batch[str]:
        del fetch_size
        assert position.anchor is not None
        self.started[position.anchor].set()
        await self.release[position.anchor].wait()
        return Batch(
            Position(position.anchor), (position.anchor,), previous=Position("previous"), next=Position("next")
        )


async def test_out_of_order_result_cannot_publish_state() -> None:
    source = RacingSource()
    loader = BatchLoader(source, 1, lambda item: item)
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
    assert results["new"] is not None and results["new"].batch.items == ("new",)


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
        Batch(Position("a", direction=Direction.FORWARD), ("a",))
    with pytest.raises(ValueError, match="negative offset"):
        Batch(Position("a", offset=-1), ("a",))


async def test_loader_rejects_results_that_contradict_capabilities() -> None:
    source = ListSource(("a", "b"))
    source.capabilities = SourceCapabilities()
    with pytest.raises(ValueError, match="uncountable source returned a total"):
        await BatchLoader(source, 2, lambda entry: entry.key).load()

    class InvalidExactSource:
        capabilities = SourceCapabilities(offsets=True, count=CountPrecision.EXACT)

        async def fetch(self, position: Position, fetch_size: int) -> Batch[Entry]:
            del position, fetch_size
            return Batch(Position(offset=1), (Entry("a"),), item_count=1)

    with pytest.raises(ValueError, match="beyond its total"):
        await BatchLoader(InvalidExactSource(), 2, lambda entry: entry.key).load(Position(offset=1))


async def test_a_list_source_returns_exact_offset_windows_and_advertises_every_capability() -> None:
    source = list_source(("a", "b", "c"))

    batch = await source.fetch(Position(offset=1), 2)

    assert batch.items == ("b", "c")
    assert batch.position == Position(offset=1)
    assert batch.has_previous
    assert not batch.has_next
    assert batch.item_count == 3
    assert source.capabilities == SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=CountPrecision.EXACT,
    )


async def test_loader_follows_source_addresses_without_reconstructing_them() -> None:
    class TokenSource:
        capabilities = SourceCapabilities(backward=True)

        async def fetch(self, position: Position, fetch_size: int) -> Batch[str]:
            assert fetch_size == 2
            match position.anchor:
                case None | "return-to-first":
                    return Batch(Position("first"), ("a", "b"), next=Position("continue-at-c"))
                case "continue-at-c":
                    return Batch(Position("second"), ("c",), previous=Position("return-to-first"))
                case _:
                    raise AssertionError(f"unexpected address {position}")

    loader = BatchLoader(TokenSource(), 2, str)
    first = await loader.load()
    assert first is not None
    second = await loader.next(first)
    assert second is not None and second.batch.items == ("c",)
    returned = await loader.previous(second)
    assert returned is not None and returned.batch.items == ("a", "b")


async def test_fixed_source_groups_are_aligned_and_counted_without_claiming_fit() -> None:
    source = list_source(tuple(str(i) for i in range(23)), fixed_groups=True)
    loader = BatchLoader(source, 10, str)
    loaded = await loader.load(Position(offset=21))
    assert loaded is not None
    assert loaded.position.offset == 20
    assert loaded.batch.items == ("20", "21", "22")
    assert source.capabilities.group_count(loaded.batch, 10) == 3
    previous = await loader.previous(loaded)
    assert previous is not None and previous.position.offset == 10
    assert source.capabilities.group_count(previous.batch, 10) == 3
    ordinary = list_source(tuple(str(i) for i in range(23)))
    assert ordinary.capabilities.group_count(loaded.batch, 10) is None


@pytest.mark.parametrize(
    "batch",
    [
        Batch(Position(offset=1), ("a", "b"), item_count=3),
        Batch(Position(), ("a",), next=Position(offset=2), item_count=3),
        Batch(Position(), ("a", "b"), next=Position(offset=4), item_count=3),
        Batch(Position(), ("a",), item_count=3),
        Batch(Position(offset=4), (), item_count=3),
    ],
)
async def test_loader_rejects_broken_fixed_group_promises(batch: Batch[str]) -> None:
    class Broken:
        capabilities = SourceCapabilities(offsets=True, count=CountPrecision.EXACT, fixed_groups=True)

        async def fetch(self, position: Position, fetch_size: int) -> Batch[str]:
            return batch

    with pytest.raises(ValueError, match="fixed-group"):
        await BatchLoader(Broken(), 2, str).load()
