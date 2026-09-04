"""An explicitly asynchronous ranking component backed by a window source."""

from collections.abc import Callable

from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.document import DocumentLike
from squid_ui.factories import action_control, action_controls, heading, note, stack
from squid_ui.interactions import ActionEvent
from squid_ui.planning.navigation import (
    NAV_FACTORY_CONTEXT,
    NavigationContext,
    default_nav,
)
from squid_ui.primitives import Lines
from squid_ui.runtime.component import Component
from squid_ui.runtime.reactivity import state
from squid_ui.runtime.resources import Failed, Pending, Ready, resource
from squid_ui.semantic import LayoutNode
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
from squid_ui_widgets._ranked import Projector, RankedEntry, RankedRows
from squid_ui_widgets._window import (
    DEFAULT_LOADING_COPY,
    LoadingCopy,
    WindowRequest,
    last_ready,
    load_window,
    source_navigation_state,
)

type SourceContentHook[RenderTargetT: DiscordTarget = DiscordTarget] = (
    ContentLike[RenderTargetT] | Callable[[int | None], ContentLike[RenderTargetT]]
)


class SourceRankedList[EntryT, RenderTargetT: DiscordTarget = DiscordTarget](Component[RenderTargetT]):
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
        title: TextLike | None = None,
        header: SourceContentHook[RenderTargetT] | None = None,
        footer: SourceContentHook[RenderTargetT] | None = None,
        empty: ContentLike[RenderTargetT] = "No entries",
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
        self.title = title
        self.header = header
        self.footer = footer
        self.empty = normalize_content(empty, name="SourceRankedList.empty")
        self.loader = WindowLoader(source, page_size, self.rows.identity_of, initial=initial_position)

        self.copy = copy

    @resource
    async def loaded(self) -> LoadedWindow[RankedEntry | EntryT]:
        """Load the requested ranked source window."""
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
        """Request the previous source window."""
        self._request = WindowRequest("previous")

    async def _next(self, _event: ActionEvent) -> None:
        """Request the next source window."""
        self._request = WindowRequest("next")

    async def _seek(self, page: int) -> None:
        """Jump to a zero-based page. Only bound when the source declared it can."""
        self._request = WindowRequest("seek", Position(offset=page * self.page_size))

    async def _retry(self, _event: ActionEvent) -> None:
        """Retry the current window request."""
        self._request = WindowRequest(self._request.operation, self._request.position)

    def _hook(
        self, hook: SourceContentHook[RenderTargetT], total: int | None, *, name: str
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        """Resolve and normalize source-aware header or footer content."""
        value = hook(total) if callable(hook) else hook
        return render_content(self, normalize_content(value, name=name), prefix=name)

    def render(self) -> DocumentLike[RenderTargetT]:
        """Render ready, pending, or failed ranking state."""
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

    def _status(self, message: TextLike, *, retry: bool = False) -> DocumentLike[RenderTargetT]:
        """Render a source status without a stale ranking window."""
        return stack(
            heading(self.title) if self.title is not None else None,
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
    ) -> DocumentLike[RenderTargetT]:
        """Render one ranked source window with navigation facts."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        nav = self.inject(NAV_FACTORY_CONTEXT, default_nav)
        window = loaded.window
        capabilities = self.source.capabilities
        navigation_state = source_navigation_state(
            loaded,
            capabilities,
            key=self.key,
            page_size=self.page_size,
            chrome=chrome,
        )
        total = window.total if capabilities.count is not CountPrecision.NONE else None
        body = (
            (Lines(self.rows.lines(window.items, window.position.offset)),)
            if window.items
            else self._hook(self.empty, total, name="empty")
        )
        navigation = (
            nav(
                NavigationContext(
                    navigation_state,
                    self._previous,
                    self._next,
                    self._seek if navigation_state.extent is not None else None,
                )
            )
            if navigation_state is not None
            else ()
        )
        numeric_footer = window_footer(chrome, self.source, loaded, self.page_size)
        return stack(
            heading(self.title) if self.title is not None else None,
            *(self._hook(self.header, total, name="header") if self.header is not None else ()),
            *body,
            note(numeric_footer) if navigation_state is not None and numeric_footer is not None else None,
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
