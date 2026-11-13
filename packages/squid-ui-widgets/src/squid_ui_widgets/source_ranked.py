"""An explicitly asynchronous ranking component backed by a window source."""

from collections.abc import Callable
from functools import partial
from typing import override

from squid_reactivity.operations import Failed, Pending
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
from squid_ui.semantic import LayoutNode
from squid_ui.sources import (
    ORIGIN,
    CountPrecision,
    LoadedWindow,
    Position,
    WindowSource,
    window_footer,
)
from squid_ui.target_types import DiscordTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets._ranked import Projector, RankedEntry, RankedRows
from squid_ui_widgets._window import (
    DEFAULT_LOADING_COPY,
    LoadingCopy,
    source_navigation_state,
)
from squid_ui_widgets.browsing import BrowseSession, Movement

type SourceContentHook[RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object] = (
    ContentLike[RenderTargetT, ContextT] | Callable[[int | None], ContentLike[RenderTargetT, ContextT]]
)


class SourceRankedList[EntryT, RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object](
    Component[RenderTargetT, ContextT]
):
    """Render source-ordered ranking windows, retaining the last page during reloads.

    The source's absolute offset determines row numbers. Header and footer callbacks
    receive None when the source does not supply a total count.
    """

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
        header: SourceContentHook[RenderTargetT, ContextT] | None = None,
        footer: SourceContentHook[RenderTargetT, ContextT] | None = None,
        empty: ContentLike[RenderTargetT, ContextT] = "No entries",
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
        self.session = BrowseSession(
            source, page_size=page_size, identity=self.rows.identity_of, initial_position=initial_position
        )

        self.copy = copy

    async def refresh(self) -> None:
        self.session.move("refresh", revision=self.session.revision)
        if self.session.operation is not None:
            await self.session.operation

    async def _move(self, movement: Movement, revision: int, _event: ActionEvent) -> None:
        self.session.move(movement, revision=revision)

    async def _seek(self, revision: int, _event: ActionEvent, page: int) -> None:
        self.session.move("seek", revision=revision, position=Position(offset=page * self.page_size))

    async def _retry(self, revision: int, _event: ActionEvent) -> None:
        self.session.retry(revision=revision)

    def _hook(
        self, hook: SourceContentHook[RenderTargetT, ContextT], total: int | None, *, name: str
    ) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
        """Resolve and normalize source-aware header or footer content."""
        value = hook(total) if callable(hook) else hook
        return normalize_content(value, name=name)

    @override
    def render(self) -> DocumentLike[RenderTargetT, ContextT]:
        """Render ready, pending, or failed ranking state."""
        current = self.session.snapshot
        operation = self.session.operation
        status = None if operation is None else operation.status
        failed = isinstance(status, Failed)
        message = self.copy.failed if failed else self.copy.loading if isinstance(status, Pending) else None
        if current is None:
            return self._status(message or self.copy.loading, retry=failed)
        return self._render_loaded(current.loaded, status=message, retry=failed)

    def _status(self, message: TextLike, *, retry: bool = False) -> DocumentLike[RenderTargetT, ContextT]:
        """Render a source status without a stale ranking window."""
        return stack(
            heading(self.title) if self.title is not None else None,
            note(message),
            action_controls(
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
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
    ) -> DocumentLike[RenderTargetT, ContextT]:
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
                    partial(self._move, "previous", self.session.revision),
                    partial(self._move, "next", self.session.revision),
                    partial(self._seek, self.session.revision) if navigation_state.extent is not None else None,
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
                action_control(self.copy.retry, partial(self._retry, self.session.revision), key=f"{self.key}.retry"),
                key=f"{self.key}.load-actions",
            )
            if retry
            else None,
        )


__all__ = ["SourceRankedList"]
