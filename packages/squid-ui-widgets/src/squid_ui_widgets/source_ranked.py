"""An explicitly asynchronous ranking component backed by a batch source."""

from collections.abc import Callable
from functools import partial
from typing import override

from squid_reactivity.operations import Failed, Pending
from squid_ui.authoring.document import DocumentLike
from squid_ui.authoring.semantic import ActionControls, LayoutNode, List, ListItem
from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.content.text import Text, TextLike
from squid_ui.factories import action_control, action_controls, heading, note, stack
from squid_ui.interactions import ActionEvent
from squid_ui.planning.pagination.navigation import (
    NAV_FACTORY_CONTEXT,
    NavigationContext,
    default_nav,
    fetch_size_button,
)
from squid_ui.runtime.definition import Component
from squid_ui.sources import (
    ORIGIN,
    BatchSource,
    CountPrecision,
    LoadedBatch,
    Position,
    batch_footer,
)
from squid_ui.target_types import RenderTarget
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets._window import (
    DEFAULT_LOADING_COPY,
    LoadingCopy,
    PageOverflow,
    source_navigation_state,
)
from squid_ui_widgets.browsing import BrowseSession, Movement
from squid_ui_widgets.ranked import Projector, RankedEntry, RankedRows

type SourceContentHook[RenderTargetT: RenderTarget = RenderTarget, ContextT = object] = (
    ContentLike[RenderTargetT, ContextT] | Callable[[int | None], ContentLike[RenderTargetT, ContextT]]
)


class SourceRankedList[EntryT, RenderTargetT: RenderTarget = RenderTarget, ContextT = object](
    Component[RenderTargetT, ContextT]
):
    """Render source-ordered ranking batches, retaining the last page during reloads.

    The source's absolute offset determines row numbers. Header and footer callbacks
    receive None when the source does not supply a total count. Each batch is one page;
    `overflow` says what a page that cannot fit does. `show_fetch_size` adds an
    items-per-page form; changing it returns to the beginning.
    """

    def __init__(
        self,
        source: BatchSource[RankedEntry | EntryT],
        *,
        key: str,
        fetch_size: int,
        identity: Projector[EntryT],
        label: Projector[EntryT] | None = None,
        value: Projector[EntryT] | None = None,
        title: TextLike | None = None,
        header: SourceContentHook[RenderTargetT, ContextT] | None = None,
        footer: SourceContentHook[RenderTargetT, ContextT] | None = None,
        empty: ContentLike[RenderTargetT, ContextT] = "No entries",
        initial_position: Position = ORIGIN,
        show_fetch_size: bool = False,
        overflow: PageOverflow = PageOverflow.SPLIT,
        copy: LoadingCopy = DEFAULT_LOADING_COPY,
    ) -> None:
        title = None if title is None else Text(title)
        self.key = require_key(key, name="SourceRankedList.key")
        if fetch_size < 1:
            message = "SourceRankedList.fetch_size must be at least 1"
            raise ValueError(message)
        self.source = source
        self.rows = RankedRows(label, value, identity)
        self.title = title
        self.header = header
        self.footer = footer
        self.empty = normalize_content(empty, name="SourceRankedList.empty")
        self.session = BrowseSession(
            source, fetch_size=fetch_size, identity=self.rows.identity_of, initial_position=initial_position
        )

        self.show_fetch_size = show_fetch_size
        self.overflow = overflow
        self.copy = copy

    @property
    def fetch_size(self) -> int:
        return self.session.fetch_size

    async def _resize(self, revision: int, _event: ActionEvent, fetch_size: int) -> None:
        self.session.resize(fetch_size, revision=revision)

    async def refresh(self) -> None:
        self.session.move("refresh", revision=self.session.revision)
        if self.session.operation is not None:
            await self.session.operation

    async def _move(self, movement: Movement, revision: int, _event: ActionEvent) -> None:
        self.session.move(movement, revision=revision)

    async def _seek(self, revision: int, _event: ActionEvent, page: int) -> None:
        self.session.move("seek", revision=revision, position=Position(offset=page * self.fetch_size))

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
        """Render a source status without a stale ranking batch."""
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
        loaded: LoadedBatch[RankedEntry | EntryT],
        *,
        status: TextLike | None = None,
        retry: bool = False,
    ) -> DocumentLike[RenderTargetT, ContextT]:
        """Render one ranked source batch with navigation facts."""
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        nav = self.inject(NAV_FACTORY_CONTEXT, default_nav)
        batch = loaded.batch
        capabilities = self.source.capabilities
        navigation_state = source_navigation_state(
            loaded,
            capabilities,
            key=self.key,
            fetch_size=self.fetch_size,
            chrome=chrome,
        )
        total = batch.item_count if capabilities.count is not CountPrecision.NONE else None
        body = (
            (
                self.overflow.listing(
                    List(
                        tuple(
                            ListItem(str(index), line)
                            for index, line in enumerate(self.rows.lines(batch.items, batch.position.offset))
                        ),
                        key=f"{self.key}.rows",
                        markers=False,
                    ),
                    fetch_size=self.fetch_size,
                    chrome=chrome,
                ),
            )
            if batch.items
            else self._hook(self.empty, total, name="empty")
        )
        size = (
            fetch_size_button(self.key, self.fetch_size, partial(self._resize, self.session.revision), chrome=chrome)
            if self.show_fetch_size
            else None
        )
        navigation = (
            nav(
                NavigationContext(
                    navigation_state,
                    partial(self._move, "previous", self.session.revision),
                    partial(self._move, "next", self.session.revision),
                    partial(self._seek, self.session.revision) if navigation_state.extent is not None else None,
                    chrome=chrome,
                    fetch_size=size,
                )
            )
            if navigation_state is not None
            else ()
        )
        numeric_footer = batch_footer(chrome, self.source, loaded, self.fetch_size)
        return stack(
            heading(self.title) if self.title is not None else None,
            *(self._hook(self.header, total, name="header") if self.header is not None else ()),
            *body,
            note(numeric_footer) if navigation_state is not None and numeric_footer is not None else None,
            *navigation,
            ActionControls((size,), key=f"{self.key}.size-controls")
            if navigation_state is None and size is not None
            else None,
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
