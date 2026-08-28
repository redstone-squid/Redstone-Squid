"""Resource-backed master-detail browsing."""

from collections.abc import Awaitable, Callable, Sequence

from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.factories import (
    action_control,
    action_controls,
    bullet,
    bullets,
    choice,
    choices,
    controlled,
    heading,
    note,
    stack,
)
from squid_ui.interactions import ActionEvent
from squid_ui.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    MountNavNode,
    NavigationContext,
    NavigationState,
    default_nav,
)
from squid_ui.runtime.component import Component, RenderResult
from squid_ui.runtime.reactivity import state
from squid_ui.runtime.resources import Failed, Pending, Ready, resource
from squid_ui.semantic import ActionControl, ChoiceEvent, ControlDisplay, Link
from squid_ui.sources import (
    ORIGIN,
    CountPrecision,
    LoadedWindow,
    Position,
    WindowLoader,
    WindowSource,
    window_footer,
)
from squid_ui.target_types import DiscordTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, render_content, require_key
from squid_ui_widgets._window import (
    DEFAULT_LOADING_COPY,
    LoadingCopy,
    WindowRequest,
    last_ready,
    load_window,
)

type BrowserDetail[ItemT, RenderTargetT: DiscordTarget = DiscordTarget] = Callable[[ItemT], ContentLike[RenderTargetT]]
type BrowserOpenHandler[ItemT] = Callable[[ActionEvent, ItemT], Awaitable[None]]
type BrowserOverview[ItemT, RenderTargetT: DiscordTarget = DiscordTarget] = Callable[
    [LoadedWindow[ItemT]], ContentLike[RenderTargetT]
]


class Browser[ItemT, RenderTargetT: DiscordTarget = DiscordTarget](Component[RenderTargetT]):
    """Browse a remote window, open one item, and act within its detail."""

    _request: WindowRequest = state(default=WindowRequest(), persist=False, opaque=True)
    opened: ItemT | None = state(None, persist=False, opaque=True)
    _detail_value: ContentLike[RenderTargetT] | None = state(None, persist=False, opaque=True)

    def __init__(
        self,
        source: WindowSource[ItemT],
        *,
        key: str = "browser",
        identity: Callable[[ItemT], str],
        label: Callable[[ItemT], TextLike],
        detail: BrowserDetail[ItemT, RenderTargetT],
        summary: Callable[[ItemT], TextLike] | None = None,
        entry_actions: Callable[[ItemT], Sequence[ActionControl | Link]] | None = None,
        overview: BrowserOverview[ItemT, RenderTargetT] | None = None,
        page_size: int = 10,
        on_open: BrowserOpenHandler[ItemT] | None = None,
        title: TextLike | None = None,
        empty: ContentLike[RenderTargetT] = "No entries",
        copy: LoadingCopy = DEFAULT_LOADING_COPY,
    ) -> None:
        self.key = require_key(key, name="Browser.key")
        if page_size < 1 or page_size > 25:
            message = "Browser.page_size must be between 1 and 25"
            raise ValueError(message)
        self.source = source
        self.identity = identity
        self.label = label
        self.detail = detail
        self.summary = summary
        self.entry_actions = entry_actions
        self.overview = overview
        self.page_size = page_size
        self.on_open = on_open
        self.title = title
        self.empty = normalize_content(empty, name="Browser.empty")
        self.copy = copy
        self.loader = WindowLoader(source, page_size, identity, initial=ORIGIN)

    @resource
    async def window(self) -> LoadedWindow[ItemT]:
        return await load_window(
            self.loader,
            self._request,
            previous=last_ready(self.window.status),
            subject="browser",
        )

    async def refresh(self) -> None:
        """Refresh around the visible anchor and wait for settlement."""
        self._request = WindowRequest()
        await self.window._load()

    async def _previous(self, _event: ActionEvent) -> None:
        self._request = WindowRequest("previous")

    async def _next(self, _event: ActionEvent) -> None:
        self._request = WindowRequest("next")

    async def _seek(self, page: int) -> None:
        self._request = WindowRequest("seek", Position(offset=page * self.page_size))

    async def _retry(self, _event: ActionEvent) -> None:
        self._request = WindowRequest(self._request.operation, self._request.position)

    def _ready(self) -> LoadedWindow[ItemT] | None:
        current = self.window.status
        if isinstance(current, Ready):
            return current.value
        if isinstance(current, Pending | Failed) and current.previous is not None:
            return current.previous.value
        return None

    async def _selected(self, event: ChoiceEvent) -> None:
        if len(event.selected) != 1:
            return
        loaded = self._ready()
        if loaded is None:
            return
        item = next((item for item in loaded.window.items if self.identity(item) == event.selected[0]), None)
        if item is None:
            return
        await self._open(event, item)

    async def _open(self, event: ActionEvent, item: ItemT) -> None:
        self.opened = item
        self._detail_value = self.detail(item)
        if self.on_open is not None:
            await self.on_open(event, item)

    async def _back(self, _event: ActionEvent) -> None:
        self.opened = None
        self._detail_value = None

    async def _adjacent(self, event: ActionEvent, delta: int) -> None:
        loaded = self._ready()
        if loaded is None or self.opened is None:
            return
        items = loaded.window.items
        index = next(
            (index for index, item in enumerate(items) if self.identity(item) == self.identity(self.opened)), -1
        )
        target = index + delta
        if 0 <= target < len(items):
            await self._open(event, items[target])

    async def _previous_item(self, event: ActionEvent) -> None:
        await self._adjacent(event, -1)

    async def _next_item(self, event: ActionEvent) -> None:
        await self._adjacent(event, 1)

    def render(self) -> RenderResult[RenderTargetT]:
        # One arm per member of `Ready | Pending | Failed`, with the `previous` case inside it.
        # Splitting on `previous` in the pattern left the match unprovably exhaustive, so the
        # checker saw a path with no return on a shape that cannot occur.
        match self.window.status:
            case Ready(value=loaded):
                return self._render_loaded(loaded)
            case Pending(previous=previous):
                if previous is None:
                    return self._status(self.copy.loading)
                return self._render_loaded(previous.value, status_text=self.copy.loading)
            case Failed(previous=previous):
                if previous is None:
                    return self._status(self.copy.failed, retry=True)
                return self._render_loaded(previous.value, status_text=self.copy.failed, retry=True)

    def _status(self, message: TextLike, *, retry: bool = False) -> RenderResult[RenderTargetT]:
        return stack(
            heading(self.title) if self.title is not None else None,
            note(message),
            action_controls(
                action_control(self.copy.retry, self._retry, key=f"{self.key}.retry"), key=f"{self.key}.retry-row"
            )
            if retry
            else None,
        )

    def _render_loaded(
        self,
        loaded: LoadedWindow[ItemT],
        *,
        status_text: TextLike | None = None,
        retry: bool = False,
    ) -> RenderResult[RenderTargetT]:
        opened = None
        if self.opened is not None:
            opened = next(
                (item for item in loaded.window.items if self.identity(item) == self.identity(self.opened)),
                None,
            )
        if opened is not None and self._detail_value is not None:
            return self._render_detail(loaded, opened, status_text=status_text, retry=retry)
        return self._render_overview(loaded, status_text=status_text, retry=retry)

    def _render_overview(
        self,
        loaded: LoadedWindow[ItemT],
        *,
        status_text: TextLike | None,
        retry: bool,
    ) -> RenderResult[RenderTargetT]:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        items = loaded.window.items
        extra = (
            render_content(self, normalize_content(self.overview(loaded), name="Browser.overview"), prefix="overview")
            if self.overview is not None
            else ()
        )
        if items:
            listing = (
                bullets(
                    *(bullet(self.summary(item) if self.summary is not None else self.label(item)) for item in items),
                    key=f"{self.key}.items",
                ),
            )
            picker = choices(
                *(choice(self.label(item), key=self.identity(item)) for item in items),
                key=f"{self.key}.open",
                selection=controlled((), self._selected),
                minimum=1,
                maximum=1,
            )
        else:
            listing = render_content(self, self.empty, prefix="empty")
            picker = None
        return stack(
            heading(self.title) if self.title is not None else None,
            *extra,
            *listing,
            picker,
            *self._navigation(loaded),
            note(status_text) if status_text is not None else None,
            action_controls(
                action_control(self.copy.retry, self._retry, key=f"{self.key}.retry"), key=f"{self.key}.retry-row"
            )
            if retry
            else None,
            note(footer) if (footer := window_footer(chrome, self.source, loaded, self.page_size)) else None,
        )

    def _navigation(self, loaded: LoadedWindow[ItemT]) -> tuple[MountNavNode, ...]:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        nav = self.inject(NAV_FACTORY_CONTEXT, default_nav)
        window = loaded.window
        capabilities = self.source.capabilities
        extent = (
            max(1, (window.total + self.page_size - 1) // self.page_size)
            if capabilities.count is CountPrecision.EXACT and capabilities.jumpable and window.total is not None
            else None
        )
        navigable = window.has_next or (capabilities.backward and window.has_previous) or (extent or 0) > 1
        if not navigable:
            return ()
        total = window.total if capabilities.count is not CountPrecision.NONE else None
        visible_range = (
            (window.position.offset + 1, window.position.offset + len(window.items))
            if capabilities.offsets and window.items
            else None
        )
        return tuple(
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
        )

    def _render_detail(
        self,
        loaded: LoadedWindow[ItemT],
        item: ItemT,
        *,
        status_text: TextLike | None,
        retry: bool,
    ) -> RenderResult[RenderTargetT]:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        items = loaded.window.items
        index = next(index for index, candidate in enumerate(items) if self.identity(candidate) == self.identity(item))
        detail_value = self._detail_value
        assert detail_value is not None
        detail = (
            (self.boundary(detail_value, key=f"detail-{self.identity(item)}"),)
            if isinstance(detail_value, Component)
            else render_content(
                self,
                normalize_content(detail_value, name="Browser.detail"),
                prefix=f"detail-{self.identity(item)}",
            )
        )
        entry_actions = tuple(self.entry_actions(item)) if self.entry_actions is not None else ()
        return stack(
            heading(self.title) if self.title is not None else None,
            *detail,
            action_controls(*entry_actions, key=f"{self.key}.entry-actions") if entry_actions else None,
            action_controls(
                action_control(chrome.back, self._back, key=f"{self.key}.back"),
                action_control(
                    chrome.previous, self._previous_item, key=f"{self.key}.item-previous", available=index > 0
                ),
                action_control(
                    chrome.next,
                    self._next_item,
                    key=f"{self.key}.item-next",
                    available=index < len(items) - 1,
                ),
                key=f"{self.key}.detail-navigation",
                display=ControlDisplay.INDIVIDUAL,
            ),
            note(status_text) if status_text is not None else None,
            action_controls(
                action_control(self.copy.retry, self._retry, key=f"{self.key}.retry"), key=f"{self.key}.retry-row"
            )
            if retry
            else None,
        )


__all__ = ["Browser", "BrowserDetail", "BrowserOpenHandler", "BrowserOverview"]
