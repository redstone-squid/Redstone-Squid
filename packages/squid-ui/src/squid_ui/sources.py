"""Fetch ordered content a batch at a time for paginated interfaces.

A BatchSource fetches items at a Position and returns a Batch with the position it
actually resolved. Positions can carry a stable item key, a numeric offset, or both;
the source decides which it can use. BatchLoader turns next, previous, and refresh
requests into fetches and discards results overtaken by a newer request.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import blake2s
from typing import Protocol

from squid_ui.chrome import Chrome
from squid_ui.content.text import TextLike


class Direction(StrEnum):
    """How a source interprets a position's anchor."""

    AROUND = "around"
    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass(frozen=True, slots=True)
class Position:
    """An optional stable item anchor plus a zero-based offset and navigation direction.

    The source defines how anchor and offset interact. Resolved batches use AROUND;
    FORWARD and BACKWARD describe requests beyond an anchor.
    """

    anchor: str | None = None
    offset: int = 0
    direction: Direction = Direction.AROUND


ORIGIN = Position()


class Chronology(StrEnum):
    """Which end of time a list starts from.

    Widgets use it to label paging by time. Declare it only for lists sorted by time: the
    labels then name where each step leads, such as "Older" for the next page of a list that
    starts with the newest items. Without it, widgets say "Previous" and "Next".
    """

    NEWEST_FIRST = "newest_first"
    OLDEST_FIRST = "oldest_first"

    def step_labels(self, chrome: Chrome) -> tuple[TextLike, TextLike]:
        """The labels for moving toward the start and toward the end of the list."""
        if self is Chronology.NEWEST_FIRST:
            return chrome.newer, chrome.older
        return chrome.older, chrome.newer


class CountPrecision(StrEnum):
    """Accuracy of the total a source returns with each batch."""

    NONE = "none"
    APPROXIMATE = "approximate"
    EXACT = "exact"


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    """Navigation, count precision, and optional fixed grouping promises.

    fixed_groups promises complete, fetch_size-aligned groups and consecutive neighbour
    addresses. It says nothing about the target space their contents require.
    """

    backward: bool = False
    offsets: bool = False
    jumpable: bool = False
    count: CountPrecision = CountPrecision.NONE
    fixed_groups: bool = False

    def group_count[T](self, batch: Batch[T], fetch_size: int) -> int | None:
        """Count declared groups, independently of target fit certification."""
        if self.fixed_groups and self.count is CountPrecision.EXACT and batch.item_count is not None:
            return (batch.item_count + fetch_size - 1) // fetch_size
        return None

    def __post_init__(self) -> None:
        if self.fixed_groups and not self.offsets:
            message = "fixed item groups require offset addresses"
            raise ValueError(message)
        if self.jumpable and not self.offsets:
            message = "a jumpable source must know offsets"
            raise ValueError(message)
        if self.count is not CountPrecision.NONE and not self.offsets:
            message = "a countable source must know offsets"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Batch[ItemT]:
    """One fetched slice and the source's addresses for its neighbours.

    The loader follows `previous` and `next` verbatim. An address may use an
    offset or a source-defined anchor; the source owns their interpretation.
    None means the source has reached that end of the collection.
    """

    position: Position
    items: tuple[ItemT, ...]
    previous: Position | None = None
    next: Position | None = None
    item_count: int | None = None

    @property
    def has_previous(self) -> bool:
        return self.previous is not None

    @property
    def has_next(self) -> bool:
        return self.next is not None

    def __post_init__(self) -> None:
        if self.position.direction is not Direction.AROUND:
            message = "a resolved Batch.position must use Direction.AROUND"
            raise ValueError(message)
        if self.position.offset < 0:
            message = "a resolved Batch.position cannot have a negative offset"
            raise ValueError(message)
        if self.item_count is not None and self.item_count < 0:
            message = "Batch.item_count cannot be negative"
            raise ValueError(message)


class BatchSource[ItemT](Protocol):
    """An async ordered source with one validated capability declaration."""

    capabilities: SourceCapabilities

    async def fetch(self, position: Position, fetch_size: int) -> Batch[ItemT]:
        """Fetch at most ``fetch_size`` items around or beyond ``position``."""
        ...


class _ListSource[ItemT]:
    capabilities: SourceCapabilities

    def __init__(self, items: Iterable[ItemT], *, fixed_groups: bool) -> None:
        self.items = tuple(items)
        self.capabilities = SourceCapabilities(
            backward=True, offsets=True, jumpable=True, count=CountPrecision.EXACT, fixed_groups=fixed_groups
        )

    async def fetch(self, position: Position, fetch_size: int) -> Batch[ItemT]:
        offset = min(max(0, position.offset), max(0, len(self.items) - 1))
        if self.capabilities.fixed_groups:
            offset = offset // fetch_size * fetch_size
        visible = self.items[offset : offset + fetch_size]
        return Batch(
            Position(offset=offset),
            visible,
            previous=Position(offset=max(0, offset - fetch_size)) if offset > 0 else None,
            next=Position(offset=offset + len(visible)) if offset + len(visible) < len(self.items) else None,
            item_count=len(self.items),
        )


def list_source[ItemT](items: Iterable[ItemT], *, fixed_groups: bool = False) -> BatchSource[ItemT]:
    """Snapshot the iterable into an exact offset-addressed source.

    fixed_groups aligns fetches to complete groups of the requested fetch_size.
    Items themselves are not copied. Fetches ignore anchors and direction and clamp
    an out-of-range offset to the last item, or zero for an empty source.
    """
    return _ListSource(items, fixed_groups=fixed_groups)


@dataclass(frozen=True, slots=True)
class LoadedBatch[ItemT]:
    """A fetched batch and the fingerprint of only its visible identities."""

    batch: Batch[ItemT]
    fingerprint: str

    @property
    def position(self) -> Position:
        return self.batch.position


def batch_fingerprint[ItemT](items: tuple[ItemT, ...], identity: Callable[[ItemT], str]) -> str:
    """Fingerprint only the identities in one visible batch."""
    digest = blake2s(digest_size=16)
    for item in items:
        encoded = identity(item).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


class BatchLoader[ItemT]:
    """Load batches and ignore results superseded by a newer request on this loader.

    Starting another request does not cancel an earlier fetch. A stale successful result
    returns None before validation; fetch exceptions still propagate. Item identities
    must be stable strings, and their ordered fingerprint excludes item content.
    """

    def __init__(
        self,
        source: BatchSource[ItemT],
        fetch_size: int,
        identity: Callable[[ItemT], str],
        *,
        initial: Position = ORIGIN,
    ) -> None:
        if fetch_size < 1:
            message = "BatchLoader.fetch_size must be at least 1"
            raise ValueError(message)
        self.source = source
        self.fetch_size = fetch_size
        self.identity = identity
        self.initial = initial
        self._request_token = 0

    async def load(
        self,
        position: Position | None = None,
        *,
        previous: LoadedBatch[ItemT] | None = None,
    ) -> LoadedBatch[ItemT] | None:
        """Load an explicit position or refresh the previous batch, returning None if superseded.

        Validate the latest result against the requested fetch_size and source capabilities.
        The source's resolved position is authoritative, including when its anchor moved.
        """
        requested = position if position is not None else previous.position if previous is not None else self.initial
        self._request_token += 1
        token = self._request_token
        fetched = await self.source.fetch(requested, self.fetch_size)
        if token != self._request_token:
            return None
        self._validate(fetched)

        fingerprint = batch_fingerprint(fetched.items, self.identity)
        return LoadedBatch(fetched, fingerprint)

    async def next(self, current: LoadedBatch[ItemT]) -> LoadedBatch[ItemT] | None:
        """Load the batch after the visible trailing identity."""
        batch = current.batch
        if not batch.has_next:
            return None
        return await self.load(batch.next, previous=current)

    async def previous(self, current: LoadedBatch[ItemT]) -> LoadedBatch[ItemT] | None:
        """Load the batch before the visible leading identity."""
        batch = current.batch
        if not self.source.capabilities.backward or not batch.has_previous:
            return None
        return await self.load(batch.previous, previous=current)

    def _validate(self, batch: Batch[ItemT]) -> None:
        if len(batch.items) > self.fetch_size:
            message = f"BatchSource returned {len(batch.items)} items for fetch_size {self.fetch_size}"
            raise ValueError(message)
        capabilities = self.source.capabilities
        if capabilities.fixed_groups:
            if batch.position.offset % self.fetch_size:
                message = "a fixed-group source returned an unaligned position"
                raise ValueError(message)
            if batch.has_next and len(batch.items) != self.fetch_size:
                message = "a fixed-group source returned an incomplete nonfinal group"
                raise ValueError(message)
            if batch.next is not None and batch.next.offset != batch.position.offset + self.fetch_size:
                message = "a fixed-group source returned a nonconsecutive next group"
                raise ValueError(message)
            if batch.previous is not None and batch.previous.offset != max(0, batch.position.offset - self.fetch_size):
                message = "a fixed-group source returned a nonconsecutive previous group"
                raise ValueError(message)
            if capabilities.count is CountPrecision.EXACT and batch.item_count is not None:
                if batch.position.offset > max(0, (batch.item_count - 1) // self.fetch_size * self.fetch_size):
                    message = "an exact source returned a position beyond its final fixed-group"
                    raise ValueError(message)
                expected = min(self.fetch_size, max(0, batch.item_count - batch.position.offset))
                if len(batch.items) != expected or batch.has_next != (
                    batch.position.offset + expected < batch.item_count
                ):
                    message = "a fixed-group source returned content inconsistent with its exact count"
                    raise ValueError(message)
        if not capabilities.backward and batch.has_previous:
            message = "a forward-only source returned has_previous=True"
            raise ValueError(message)
        if capabilities.count is CountPrecision.NONE and batch.item_count is not None:
            message = "an uncountable source returned a total"
            raise ValueError(message)
        if capabilities.count is not CountPrecision.NONE and batch.item_count is None:
            message = "a countable source omitted its total"
            raise ValueError(message)
        if (
            capabilities.count is CountPrecision.EXACT
            and batch.item_count is not None
            and batch.position.offset + len(batch.items) > batch.item_count
        ):
            message = "an exact source returned a batch beyond its total"
            raise ValueError(message)


def batch_footer[ItemT](
    chrome: Chrome, source: BatchSource[ItemT], loaded: LoadedBatch[ItemT], fetch_size: int
) -> TextLike | None:
    """Describe only the numeric position the source's capabilities support."""
    batch = loaded.batch
    capabilities = source.capabilities
    if not batch.items or not capabilities.offsets:
        return None
    first = batch.position.offset + 1
    last = batch.position.offset + len(batch.items)
    if (pages := capabilities.group_count(batch, fetch_size)) is not None and capabilities.jumpable:
        # One authored group needs no group-number footer; target fit is separate.
        return chrome.page_footer(batch.position.offset // fetch_size + 1, pages) if pages > 1 else None
    if capabilities.count is CountPrecision.EXACT and batch.item_count is not None:
        return chrome.total_range_footer(first, last, batch.item_count)
    if capabilities.count is CountPrecision.APPROXIMATE and batch.item_count is not None:
        return chrome.approximate_total_footer(first, last, batch.item_count)
    return chrome.range_footer(first, last)


__all__ = [
    "ORIGIN",
    "Batch",
    "BatchLoader",
    "BatchSource",
    "Chronology",
    "CountPrecision",
    "Direction",
    "LoadedBatch",
    "Position",
    "SourceCapabilities",
    "list_source",
]
