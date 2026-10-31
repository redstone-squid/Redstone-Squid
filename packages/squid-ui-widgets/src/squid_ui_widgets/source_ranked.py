"""An explicitly asynchronous ranking component backed by a window source."""

from collections.abc import Callable
from typing import Any

from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.factories import action_control, action_controls, heading, note, stack
from squid_ui.interactions import ActionEvent
from squid_ui.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    NavigationContext,
    NavigationState,
    default_nav,
)
from squid_ui.primitives import Lines
from squid_ui.runtime.component import Component, RenderResult
from squid_ui.runtime.reactivity import state
from squid_ui.runtime.resources import Failed, Pending, Ready, resource
from squid_ui.semantic import AnyLayoutNode
from squid_ui.sources import (
    ORIGIN,
    CountPrecision,
    LoadedWindow,
    Position,
    WindowLoader,
    WindowSource,
    window_footer,
)
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, render_content, require_key
from squid_ui_widgets._ranked import Projector, RankedEntry, RankedRows
from squid_ui_widgets._window import (
    DEFAULT_LOADING_COPY,
    LoadingCopy,
    WindowRequest,
    last_ready,
    load_window,
)

type SourceContentHook = ContentLike | Callable[[int | None], ContentLike]


class SourceRankedList[EntryT](Component[Any]):
    """Render a ranking whose visible async resource is backed by a window source."""

    _request: WindowRequest = state(default=WindowRequest(), persist=False, opaque=True)

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
        copy: LoadingCopy = DEFAULT_LOADING_COPY,
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

        self.copy = copy

    @resource
    async def loaded(self) -> LoadedWindow[RankedEntry | EntryT]:
        return await load_window(
            self.loader,
            self._request,
            previous=last_ready(self.loaded.status),
            subject="source",
        )

    async def refresh(self) -> None:
        """Refresh around the currently visible anchor."""
        self._request = WindowRequest()
        await self.loaded._load()

    async def _previous(self, _event: ActionEvent) -> None:
        self._request = WindowRequest("previous")

    async def _next(self, _event: ActionEvent) -> None:
        self._request = WindowRequest("next")

    async def _seek(self, page: int) -> None:
        """Jump to a zero-based page. Only bound when the source declared it can."""
        self._request = WindowRequest("seek", Position(offset=page * self.page_size))

    async def _retry(self, _event: ActionEvent) -> None:
        self._request = WindowRequest(self._request.operation, self._request.position)

    def _hook(self, hook: SourceContentHook, total: int | None, *, name: str) -> tuple[AnyLayoutNode, ...]:
        value = hook(total) if callable(hook) else hook
        return render_content(self, normalize_content(value, name=name), prefix=name)

    def render(self) -> RenderResult[Any]:
        # One arm per member of `Ready | Pending | Failed`, with the `previous` case inside it.
        # Splitting on `previous` in the pattern left the match unprovably exhaustive, so the
        # checker saw a path with no return on a shape that cannot occur.
        match self.loaded.status:
            case Ready(value=loaded):
                return self._render_loaded(loaded)
            case Pending(previous=previous):
                if previous is None:
                    return self._status(self.copy.loading)
                return self._render_loaded(previous.value, status=self.copy.loading)
            case Failed(previous=previous):
                if previous is None:
                    return self._status(self.copy.failed, retry=True)
                return self._render_loaded(previous.value, status=self.copy.failed, retry=True)

    def _status(self, message: TextLike, *, retry: bool = False) -> RenderResult[Any]:
        return stack(
            heading(self.heading) if self.heading is not None else None,
            note(message),
            action_controls(
                action_control(self.copy.retry, self._retry, key=f"{self.key}.retry"),
                key=f"{self.key}.load-actions",
            )
            if retry
            else None,
        )

    def _render_loaded(
        self,
        loaded: LoadedWindow[RankedEntry | EntryT],
        *,
        status: TextLike | None = None,
        retry: bool = False,
    ) -> RenderResult[Any]:
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
        extent = (
            max(1, (window.total + self.page_size - 1) // self.page_size)
            if capabilities.count is CountPrecision.EXACT and capabilities.jumpable and window.total is not None
            else None
        )
        # A cursor that can address a page is navigable on every page, the last one included.
        # Without this a forward-only jumpable source loses its whole navigation the moment a
        # jump lands on the end -- including the control that could take the reader back.
        navigable = window.has_next or (capabilities.backward and window.has_previous) or (extent or 0) > 1
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
                        page=window.position.offset // self.page_size if capabilities.offsets else None,
                        visible_range=visible_range,
                        total=total,
                        count=capabilities.count,
                        seek_key=f"{self.key}.seek",
                        seek_label=chrome.jump_to_page,
                        page_option=chrome.page_option,
                    ),
                    self._previous,
                    self._next,
                    self._seek if extent is not None else None,
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
            note(status) if status is not None else None,
            action_controls(
                action_control(self.copy.retry, self._retry, key=f"{self.key}.retry"),
                key=f"{self.key}.load-actions",
            )
            if retry
            else None,
        )


__all__ = ["SourceRankedList"]
