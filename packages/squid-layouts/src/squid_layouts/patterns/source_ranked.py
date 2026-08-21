"""An explicitly asynchronous ranking component backed by a window source."""

from collections.abc import Awaitable, Callable

from squid_layouts.actions import ActionEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.factories import heading, note, stack
from squid_layouts.patterns._content import ContentLike, normalize_content, require_key
from squid_layouts.patterns._ranked import Projector, RankedEntry, RankedRows
from squid_layouts.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    NavigationContext,
    NavigationState,
    default_nav,
)
from squid_layouts.primitives import Lines
from squid_layouts.runtime.component import Component, RenderResult
from squid_layouts.runtime.reactivity import state
from squid_layouts.semantic import LayoutNode
from squid_layouts.sources import (
    ORIGIN,
    CountPrecision,
    LoadedWindow,
    Position,
    WindowLoader,
    WindowSource,
    window_footer,
)
from squid_layouts.text import TextLike

type SourceContentHook = ContentLike | Callable[[int | None], ContentLike]


class SourceRankedList[EntryT](Component):
    """Render a ranking whose windows are loaded by interaction handlers."""

    loaded: LoadedWindow[RankedEntry | EntryT] | None = state(None, persist=False, copy="ref")

    def __init__(
        self,
        source: WindowSource[RankedEntry | EntryT],
        *,
        key: str,
        page_size: int,
        identity: Projector[EntryT],
        label: Projector[EntryT] | None = None,
        value: Projector[EntryT] | None = None,
        heading: TextLike | None = None,
        header: SourceContentHook | None = None,
        footer: SourceContentHook | None = None,
        empty: ContentLike = "No entries",
        initial_position: Position = ORIGIN,
    ) -> None:
        self.key = require_key(key, name="SourceRankedList.key")
        if page_size < 1:
            message = "SourceRankedList.page_size must be at least 1"
            raise ValueError(message)
        self.source = source
        self.page_size = page_size
        self.rows = RankedRows(label, value, identity)
        self.heading = heading
        self.header = header
        self.footer = footer
        self.empty = normalize_content(empty, name="SourceRankedList.empty")
        self.loader = WindowLoader(source, page_size, self.rows.identity_of, initial=initial_position)

    async def on_load(self) -> None:
        loaded = await self.loader.load()
        if loaded is None:
            message = "initial source window was superseded before it loaded"
            raise LayoutInvariantError(message)
        self.loaded = loaded

    async def refresh(self) -> None:
        """Refresh around the currently visible anchor."""
        if self.loaded is None:
            return
        await self._publish(self.loader.load(previous=self.loaded))

    async def _previous(self, _event: ActionEvent) -> None:
        if self.loaded is not None:
            await self._publish(self.loader.previous(self.loaded))

    async def _next(self, _event: ActionEvent) -> None:
        if self.loaded is not None:
            await self._publish(self.loader.next(self.loaded))

    async def _publish(self, pending: Awaitable[LoadedWindow[RankedEntry | EntryT] | None]) -> None:
        loaded = await pending
        if loaded is not None:
            self.loaded = loaded

    def _hook(self, hook: SourceContentHook, total: int | None, *, name: str) -> tuple[LayoutNode, ...]:
        value = hook(total) if callable(hook) else hook
        content = normalize_content(value, name=name)
        return tuple(
            self.embed(item, key=f"{name}-{index}") if isinstance(item, Component) else item
            for index, item in enumerate(content)
        )

    def render(self) -> RenderResult:
        loaded = self.loaded
        if loaded is None:
            message = "SourceRankedList rendered before its initial window loaded"
            raise LayoutInvariantError(message)
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        nav = self.inject(NAV_FACTORY_CONTEXT, default_nav)
        window = loaded.window
        capabilities = self.source.capabilities
        total = window.total if capabilities.count is not CountPrecision.NONE else None
        body = (
            (Lines(self.rows.lines(window.items, window.position.offset)),)
            if window.items
            else self._hook(self.empty, total, name="empty")
        )
        navigable = window.has_next or (capabilities.backward and window.has_previous)
        extent = (
            max(1, (window.total + self.page_size - 1) // self.page_size)
            if capabilities.count is CountPrecision.EXACT and capabilities.jumpable and window.total is not None
            else None
        )
        visible_range = (
            (window.position.offset + 1, window.position.offset + len(window.items))
            if capabilities.offsets and window.items
            else None
        )
        navigation = (
            nav(
                NavigationContext(
                    NavigationState(
                        key=self.key,
                        position=window.position,
                        has_previous=window.has_previous,
                        has_next=window.has_next,
                        backward=capabilities.backward,
                        previous_label=chrome.older,
                        next_label=chrome.newer,
                        previous_key=f"{self.key}.previous",
                        next_key=f"{self.key}.next",
                        extent=extent,
                        visible_range=visible_range,
                        total=total,
                        count=capabilities.count,
                    ),
                    self._previous,
                    self._next,
                )
            )
            if navigable
            else ()
        )
        numeric_footer = window_footer(chrome, self.source, loaded, self.page_size)
        return stack(
            heading(self.heading) if self.heading is not None else None,
            *(self._hook(self.header, total, name="header") if self.header is not None else ()),
            *body,
            note(numeric_footer) if navigable and numeric_footer is not None else None,
            *navigation,
            *(self._hook(self.footer, total, name="footer") if self.footer is not None else ()),
        )


__all__ = ["SourceRankedList"]
