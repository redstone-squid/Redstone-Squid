"""Own a collection's loaded batch and focused item as one browsing session.

Commands are relative to a displayed revision. Fetches produce candidates and
publish them in a fresh action only if that revision is still current. Components
observe `operation.status` to let their host schedule work and show progress.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from squid_reactivity import Reactive, computed, state, transaction
from squid_reactivity.operations import OperationDefinition, OperationExecution, Pending, ProgressReporter
from squid_ui.sources import BatchLoader, BatchSource, LoadedBatch, Position

type Movement = Literal["refresh", "previous", "next", "seek", "previous-item", "next-item"]


@dataclass(frozen=True, slots=True)
class BrowseSnapshot[ItemT]:
    """One accepted batch, with an optional focused item."""

    loaded: LoadedBatch[ItemT]
    focus: str | None = None
    notice: Literal["removed", "end"] | None = None


@dataclass(frozen=True, slots=True)
class _Request[ItemT]:
    source: BatchSource[ItemT]
    base: BrowseSnapshot[ItemT] | None
    movement: Movement
    position: Position | None = None


class BrowseSession[ItemT](Reactive):
    """Reusable browsing state, independent of detail rendering and frontend events.

    A source instance identifies one collection, including its filters and ordering.
    Replace it with `reset` when those change. Pass the rendered `revision` to commands
    so an old control cannot move a newer collection. Forward-only sources still allow
    stepping backwards within their currently loaded batch.
    """

    snapshot: BrowseSnapshot[ItemT] | None = state(None, opaque=True)
    fetch_size: int = state(10)
    revision: int = state(0)
    visit: int = state(0)
    operation: OperationExecution[None, None] | None = state(None, opaque=True)
    _request: _Request[ItemT] | None = state(None, opaque=True)
    source: BatchSource[ItemT] = state(opaque=True)

    def __init__(
        self,
        source: BatchSource[ItemT],
        *,
        identity: Callable[[ItemT], str],
        fetch_size: int = 10,
        initial: LoadedBatch[ItemT] | None = None,
        initial_position: Position | None = None,
    ) -> None:
        if fetch_size < 1:
            msg = "BrowseSession.fetch_size must be positive"
            raise ValueError(msg)
        self.source = source
        self.identity = identity
        self.fetch_size = fetch_size
        if initial is None:
            self._start(_Request(source, None, "refresh", initial_position))
        else:
            self.replace(initial)

    @computed
    def focus(self) -> str | None:
        return None if self.snapshot is None else self.snapshot.focus

    @property
    def focused_item(self) -> ItemT | None:
        current = self.snapshot
        if current is None or current.focus is None:
            return None
        return next(item for item in current.loaded.batch.items if self.identity(item) == current.focus)

    @property
    def busy(self) -> bool:
        return self.operation is not None and isinstance(self.operation.status, Pending)

    def replace(self, loaded: LoadedBatch[ItemT]) -> None:
        """Seed an already fetched overview and supersede any in-flight request."""
        self._publish(BrowseSnapshot(loaded))

    def reset(self, source: BatchSource[ItemT]) -> None:
        """Start a different collection without retaining its predecessor's focus."""
        with transaction():
            self.source = source
            self.visit += 1
            self.snapshot = None
            self.revision += 1
            self._start(_Request(source, None, "refresh"))

    def resize(self, fetch_size: int, *, revision: int) -> None:
        """Reload from the beginning with a new item count, ignoring stale controls."""
        if fetch_size < 1:
            message = "BrowseSession.fetch_size must be positive"
            raise ValueError(message)
        if revision != self.revision or self.busy or fetch_size == self.fetch_size:
            return
        with transaction():
            self.fetch_size = fetch_size
            self.reset(self.source)

    def open(self, key: str, *, revision: int) -> None:
        current = self.snapshot
        if revision != self.revision or current is None:
            return
        if any(self.identity(item) == key for item in current.loaded.batch.items):
            self._publish(replace(current, focus=key, notice=None))

    def back(self, *, revision: int) -> None:
        if revision != self.revision or self.snapshot is None:
            return
        self._publish(replace(self.snapshot, focus=None, notice=None))

    def can_step(self, direction: Literal[-1, 1]) -> bool:
        current = self.snapshot
        if current is None or current.focus is None:
            return False
        items = current.loaded.batch.items
        index = next(index for index, item in enumerate(items) if self.identity(item) == current.focus)
        if 0 <= index + direction < len(items):
            return True
        batch = current.loaded.batch
        return batch.has_next if direction == 1 else self.source.capabilities.backward and batch.has_previous

    def move(self, movement: Movement, *, revision: int, position: Position | None = None) -> None:
        """Request a movement from a displayed revision; repeated pending clicks coalesce."""
        if revision != self.revision or self.busy:
            return
        current = self.snapshot
        if movement in ("previous-item", "next-item"):
            direction = -1 if movement == "previous-item" else 1
            if not self.can_step(direction) or current is None:
                return
            items = current.loaded.batch.items
            index = next(index for index, item in enumerate(items) if self.identity(item) == current.focus)
            if 0 <= index + direction < len(items):
                self.open(self.identity(items[index + direction]), revision=revision)
                return
        elif movement in ("previous", "next"):
            if current is None:
                return
            batch = current.loaded.batch
            if movement == "previous" and not (self.source.capabilities.backward and batch.has_previous):
                return
            if movement == "next" and not batch.has_next:
                return
        elif movement == "seek" and (not self.source.capabilities.jumpable or position is None):
            return
        self._start(_Request(self.source, current, movement, position))

    def retry(self, *, revision: int) -> None:
        if revision == self.revision and not self.busy and self._request is not None:
            self._start(self._request)

    def _publish(self, snapshot: BrowseSnapshot[ItemT]) -> None:
        with transaction():
            if self.snapshot is None or self.snapshot.focus != snapshot.focus:
                self.visit += 1
            self.snapshot = snapshot
            self.revision += 1
            self.operation = None
            self._request = None

    def _start(self, request: _Request[ItemT]) -> None:
        with transaction() as tx:
            self.revision += 1
            revision = self.revision

            async def resolve(_progress: ProgressReporter[None]) -> None:
                candidate = await self._resolve(request)
                with execution.start_transaction("browse.accept"):
                    if self.revision == revision:
                        self._publish(candidate)

            execution = OperationDefinition(resolve, name="browse.move", initial=None).start(caused_by=tx.context)
            self._request = request
            self.operation = execution

    async def _resolve(self, request: _Request[ItemT]) -> BrowseSnapshot[ItemT]:
        loader = BatchLoader(request.source, self.fetch_size, self.identity)
        base = request.base
        current = None if base is None else base.loaded
        movement = request.movement
        if current is not None and movement in ("previous", "previous-item", "next", "next-item"):
            backward = movement in ("previous", "previous-item")
            address = current.batch.previous if backward else current.batch.next
            seen: set[Position] = set()
            while address is not None:
                if address in seen or len(seen) >= 32:
                    msg = "source navigation repeated an address or exceeded 32 empty batches"
                    raise ValueError(msg)
                seen.add(address)
                loaded = await loader.load(address)
                assert loaded is not None
                if loaded.batch.items:
                    focus = None
                    if movement.endswith("-item"):
                        keys = tuple(self.identity(item) for item in loaded.batch.items)
                        target = len(keys) - 1 if backward else 0
                        if base is not None and base.focus in keys:
                            target = keys.index(base.focus) + (-1 if backward else 1)
                        if not 0 <= target < len(keys):
                            address = loaded.batch.previous if backward else loaded.batch.next
                            continue
                        focus = keys[target]
                    return BrowseSnapshot(loaded, focus)
                address = loaded.batch.previous if backward else loaded.batch.next
            # Data may have disappeared after the displayed source advertised another batch.
            batch = replace(current.batch, previous=None) if backward else replace(current.batch, next=None)
            assert base is not None
            return replace(base, loaded=replace(current, batch=batch), notice="end")
        loaded = await loader.load(request.position, previous=current)
        assert loaded is not None
        focus = base.focus if base is not None and movement == "refresh" else None
        removed = focus is not None and not any(self.identity(item) == focus for item in loaded.batch.items)
        return BrowseSnapshot(loaded, None if removed else focus, "removed" if removed else None)


__all__ = ["BrowseSession", "BrowseSnapshot"]
