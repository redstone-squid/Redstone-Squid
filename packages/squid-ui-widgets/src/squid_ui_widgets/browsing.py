"""Own a collection's loaded window and focused item as one browsing session.

Commands are relative to a displayed revision. Fetches produce candidates and
publish them in a fresh action only if that revision is still current. Components
observe `operation.status` to let their host schedule work and show progress.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from squid_reactivity import Reactive, computed, state, transaction
from squid_reactivity.operations import OperationDefinition, OperationExecution, Pending, ProgressReporter
from squid_ui.sources import LoadedWindow, Position, WindowLoader, WindowSource

type Movement = Literal["refresh", "previous", "next", "seek", "previous-item", "next-item"]


@dataclass(frozen=True, slots=True)
class BrowseSnapshot[ItemT]:
    """One accepted window, with an optional focus and an overview reveal anchor."""

    loaded: LoadedWindow[ItemT]
    focus: str | None = None
    reveal: str | None = None
    notice: Literal["removed", "end"] | None = None


@dataclass(frozen=True, slots=True)
class _Request[ItemT]:
    source: WindowSource[ItemT]
    base: BrowseSnapshot[ItemT] | None
    movement: Movement
    position: Position | None = None


class BrowseSession[ItemT](Reactive):
    """Reusable browsing state, independent of detail rendering and frontend events.

    A source instance identifies one collection, including its filters and ordering.
    Replace it with `reset` when those change. Pass the rendered `revision` to commands
    so an old control cannot move a newer collection. Forward-only sources still allow
    stepping backwards within their currently loaded window.
    """

    snapshot: BrowseSnapshot[ItemT] | None = state(None, persist=False, opaque=True)
    revision: int = state(0, persist=False)
    operation: OperationExecution[None, None] | None = state(None, persist=False, opaque=True)
    _request: _Request[ItemT] | None = state(None, persist=False, opaque=True)
    source: WindowSource[ItemT] = state(persist=False, opaque=True)

    def __init__(
        self,
        source: WindowSource[ItemT],
        *,
        identity: Callable[[ItemT], str],
        page_size: int = 10,
        initial: LoadedWindow[ItemT] | None = None,
    ) -> None:
        if page_size < 1:
            msg = "BrowseSession.page_size must be positive"
            raise ValueError(msg)
        self.source = source
        self.identity = identity
        self.page_size = page_size
        if initial is None:
            self.move("refresh", revision=self.revision)
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
        return next(item for item in current.loaded.window.items if self.identity(item) == current.focus)

    @property
    def busy(self) -> bool:
        return self.operation is not None and isinstance(self.operation.status, Pending)

    def replace(self, loaded: LoadedWindow[ItemT]) -> None:
        """Seed an already fetched overview and supersede any in-flight request."""
        self._publish(BrowseSnapshot(loaded))

    def reset(self, source: WindowSource[ItemT]) -> None:
        """Start a different collection without retaining its predecessor's focus."""
        with transaction():
            self.source = source
            self.snapshot = None
            self.revision += 1
            self._start(_Request(source, None, "refresh"))

    def open(self, key: str, *, revision: int) -> None:
        current = self.snapshot
        if revision != self.revision or current is None:
            return
        if any(self.identity(item) == key for item in current.loaded.window.items):
            self._publish(replace(current, focus=key, reveal=key, notice=None))

    def back(self, *, revision: int) -> None:
        if revision != self.revision or self.snapshot is None:
            return
        self._publish(replace(self.snapshot, focus=None, notice=None))

    def can_step(self, direction: Literal[-1, 1]) -> bool:
        current = self.snapshot
        if current is None or current.focus is None:
            return False
        items = current.loaded.window.items
        index = next(index for index, item in enumerate(items) if self.identity(item) == current.focus)
        if 0 <= index + direction < len(items):
            return True
        window = current.loaded.window
        return window.has_next if direction == 1 else self.source.capabilities.backward and window.has_previous

    def move(self, movement: Movement, *, revision: int, position: Position | None = None) -> None:
        """Request a movement from a displayed revision; repeated pending clicks coalesce."""
        if revision != self.revision or self.busy:
            return
        current = self.snapshot
        if movement in ("previous-item", "next-item"):
            direction = -1 if movement == "previous-item" else 1
            if not self.can_step(direction) or current is None:
                return
            items = current.loaded.window.items
            index = next(index for index, item in enumerate(items) if self.identity(item) == current.focus)
            if 0 <= index + direction < len(items):
                self.open(self.identity(items[index + direction]), revision=revision)
                return
        elif movement in ("previous", "next"):
            if current is None:
                return
            window = current.loaded.window
            if movement == "previous" and not (self.source.capabilities.backward and window.has_previous):
                return
            if movement == "next" and not window.has_next:
                return
        elif movement == "seek" and (not self.source.capabilities.jumpable or position is None):
            return
        self._start(_Request(self.source, current, movement, position))

    def retry(self, *, revision: int) -> None:
        if revision == self.revision and not self.busy and self._request is not None:
            self._start(self._request)

    def _publish(self, snapshot: BrowseSnapshot[ItemT]) -> None:
        with transaction():
            self.snapshot = snapshot
            self.revision += 1
            self.operation = None
            self._request = None

    def _start(self, request: _Request[ItemT]) -> None:
        with transaction():
            self.revision += 1
            revision = self.revision

            async def resolve(_progress: ProgressReporter[None]) -> None:
                candidate = await self._resolve(request)
                with execution.start_action("browse.accept"):
                    if self.revision == revision:
                        self._publish(candidate)

            execution = OperationDefinition(resolve, name="browse.move", initial=None).start()
            self._request = request
            self.operation = execution

    async def _resolve(self, request: _Request[ItemT]) -> BrowseSnapshot[ItemT]:
        loader = WindowLoader(request.source, self.page_size, self.identity)
        base = request.base
        current = None if base is None else base.loaded
        movement = request.movement
        if current is not None and movement in ("previous", "previous-item", "next", "next-item"):
            backward = movement in ("previous", "previous-item")
            address = current.window.previous if backward else current.window.next
            seen: set[Position] = set()
            while address is not None:
                if address in seen:
                    msg = "source navigation repeated a continuation address"
                    raise ValueError(msg)
                seen.add(address)
                loaded = await loader.load(address)
                assert loaded is not None
                if loaded.window.items:
                    focus = None
                    if movement.endswith("-item"):
                        focus = self.identity(loaded.window.items[-1 if backward else 0])
                    return BrowseSnapshot(loaded, focus, focus)
                address = loaded.window.previous if backward else loaded.window.next
            # Data may have disappeared after the displayed source advertised another window.
            window = replace(current.window, previous=None) if backward else replace(current.window, next=None)
            assert base is not None
            return replace(base, loaded=replace(current, window=window), notice="end")
        loaded = await loader.load(request.position, previous=current)
        assert loaded is not None
        focus = base.focus if base is not None and movement == "refresh" else None
        removed = focus is not None and not any(self.identity(item) == focus for item in loaded.window.items)
        return BrowseSnapshot(loaded, None if removed else focus, focus, "removed" if removed else None)


__all__ = ["BrowseSession", "BrowseSnapshot"]
