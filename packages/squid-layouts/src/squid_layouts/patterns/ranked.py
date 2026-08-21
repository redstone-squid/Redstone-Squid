"""A ranked collection whose explicit page works in either shell."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from squid_layouts.actions import ActionEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME, Chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.factories import action, actions, heading, note, stack
from squid_layouts.patterns._content import ContentLike, display_text, normalize_content, require_key
from squid_layouts.patterns._paging import window
from squid_layouts.patterns.shells import ComponentShell, PatternControls
from squid_layouts.primitives import Lines
from squid_layouts.runtime.component import Component, RenderResult
from squid_layouts.runtime.reactivity import state
from squid_layouts.semantic import LayoutNode
from squid_layouts.sources import Position, WindowCursor, WindowSource, window_footer
from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True)
class RankedEntry:
    """An already-ranked row for callers that do not need projection callbacks."""

    label: TextLike
    value: object
    key: str = ""


@dataclass(frozen=True, slots=True)
class RankedListState:
    """Serializable zero-based page for :class:`RankedList`."""

    page: int = 0


type Projector[EntryT] = str | Callable[[EntryT], object]
type ContentHook = ContentLike | Callable[[int | None], ContentLike]


class RankedList[EntryT]:
    """Render caller-ranked values through the shared explicit-page policy."""

    def __init__(
        self,
        entries: Iterable[RankedEntry | EntryT] | None = None,
        *,
        source: WindowSource[RankedEntry | EntryT] | None = None,
        key: str,
        label: Projector[EntryT] | None = None,
        value: Projector[EntryT] | None = None,
        identity: Projector[EntryT] | None = None,
        heading: TextLike | None = None,
        header: ContentHook | None = None,
        footer: ContentHook | None = None,
        page_size: int | None = None,
        top_n: int | None = None,
        limit: int | None = None,
        empty: ContentLike = "No entries",
        initial_page: int = 0,
        initial_position: Position | None = None,
    ) -> None:
        self.key = require_key(key, name="RankedList.key")
        if (entries is None) == (source is None):
            message = "RankedList needs exactly one of entries or source"
            raise TypeError(message)
        if page_size is not None and page_size < 1:
            message = "RankedList.page_size must be at least 1"
            raise ValueError(message)
        if top_n is not None and top_n < 1:
            message = "RankedList.top_n must be at least 1"
            raise ValueError(message)
        if limit is not None and limit < 1:
            message = "RankedList.limit must be at least 1"
            raise ValueError(message)
        if top_n is not None and limit is not None:
            message = "RankedList accepts either top_n or limit, not both"
            raise TypeError(message)
        if source is not None and page_size is None:
            message = "a source-backed RankedList needs page_size"
            raise TypeError(message)
        if source is not None and (top_n is not None or limit is not None):
            message = "a source-backed RankedList delegates limits to its source"
            raise TypeError(message)
        if source is None and initial_position is not None:
            message = "initial_position is only available with a source"
            raise TypeError(message)
        self.entries = tuple(entries) if entries is not None else ()
        self.source = source
        self.label = label
        self.value = value
        self.identity = identity
        self.heading = heading
        self.header = header
        self.footer = footer
        self.page_size = page_size
        self.top_n = top_n if top_n is not None else limit
        self.empty = normalize_content(empty, name="RankedList.empty")
        self._initial_state = RankedListState(initial_page)
        self._initial_position = initial_position or Position(offset=initial_page * (page_size or 1))

    @property
    def initial_state(self) -> RankedListState:
        return self._initial_state

    def component(self, *, initial: RankedListState | None = None) -> ComponentShell[RankedListState]:
        """Build the in-memory shell for this ranking."""
        if self.source is not None:
            return _SourceRankedListComponent(self, initial=initial)
        return ComponentShell(self, initial=initial)

    @staticmethod
    def _project(entry: EntryT, projector: Projector[EntryT]) -> object:
        if callable(projector):
            return projector(entry)
        if isinstance(entry, Mapping):
            try:
                return entry[projector]
            except KeyError as error:
                message = f"ranked entry has no key {projector!r}"
                raise ValueError(message) from error
        try:
            return getattr(entry, projector)
        except AttributeError as error:
            message = f"ranked entry has no attribute {projector!r}"
            raise ValueError(message) from error

    def _row_values(self, entry: RankedEntry | EntryT) -> tuple[object, object, str]:
        if isinstance(entry, RankedEntry):
            if self.label is not None or self.value is not None:
                message = "projectors cannot be combined with RankedEntry values"
                raise TypeError(message)
            return entry.label, entry.value, entry.key
        if self.label is None or self.value is None:
            if isinstance(entry, tuple) and len(entry) == 2:
                return entry[0], entry[1], ""
            message = "RankedList needs label and value projectors for non-tuple entries"
            raise TypeError(message)
        return self._project(entry, self.label), self._project(entry, self.value), ""

    def transition(
        self,
        state: RankedListState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> RankedListState:
        del values, submitted
        if action == "previous":
            return RankedListState(max(0, state.page - 1))
        if action == "next":
            return RankedListState(state.page + 1)
        return state

    def _hook(
        self,
        hook: ContentHook,
        total: int,
        controls: PatternControls[RankedListState],
        *,
        name: str,
    ) -> tuple[LayoutNode, ...]:
        value = hook(total) if callable(hook) else hook
        return controls.content(normalize_content(value, name=name), prefix=name)

    def render(self, state: RankedListState, controls: PatternControls[RankedListState]) -> RenderResult:
        if self.source is not None:
            message = "a source-backed RankedList requires component(); RouterShell accepts materialized entries only"
            raise TypeError(message)
        displayed = self.entries if self.top_n is None else self.entries[: self.top_n]
        total = len(displayed)
        if self.page_size is None:
            visible, page, pages = displayed, 0, 1
        else:
            visible, page, pages = window(
                displayed,
                key=self.key,
                page=state.page,
                per_page=self.page_size,
                chrome=controls.chrome,
                identity=self._identity,
            )
        offset = page * (self.page_size or max(1, total))
        body = (
            (
                Lines(
                    tuple(
                        f"{rank}. **{display_text(label)}** — {display_text(value)}"
                        for rank, entry in enumerate(visible, offset + 1)
                        for label, value, _entry_key in (self._row_values(entry),)
                    )
                ),
            )
            if visible
            else controls.content(self.empty, prefix="empty")
        )
        pager = (
            actions(
                controls.action(
                    controls.chrome.previous,
                    "previous",
                    key=f"{self.key}.previous",
                    available=page > 0,
                ),
                controls.action(
                    controls.chrome.next,
                    "next",
                    key=f"{self.key}.next",
                    available=page < pages - 1,
                ),
                key=f"{self.key}.pager",
            )
            if pages > 1
            else None
        )
        return stack(
            heading(self.heading) if self.heading is not None else None,
            *(self._hook(self.header, total, controls, name="header") if self.header is not None else ()),
            *body,
            note(controls.chrome.page_footer(page + 1, pages)) if pages > 1 else None,
            pager,
            *(self._hook(self.footer, total, controls, name="footer") if self.footer is not None else ()),
        )

    def _identity(self, entry: RankedEntry | EntryT) -> str:
        if isinstance(entry, RankedEntry) and entry.key:
            return entry.key
        if self.identity is not None and not isinstance(entry, RankedEntry):
            return str(self._project(entry, self.identity))
        return repr(entry)

    def _source_hook(
        self,
        hook: ContentHook,
        total: int | None,
        owner: Component,
        *,
        name: str,
    ) -> tuple[LayoutNode, ...]:
        value = hook(total) if callable(hook) else hook
        content = normalize_content(value, name=name)
        return tuple(
            owner.embed(item, key=f"{name}-{index}") if isinstance(item, Component) else item
            for index, item in enumerate(content)
        )

    def _render_source(
        self,
        owner: _SourceRankedListComponent[EntryT],
        chrome: Chrome,
        cursor: WindowCursor[RankedEntry | EntryT],
    ) -> RenderResult:
        visible = cursor.window
        if visible is None or self.source is None:
            message = "source-backed RankedList rendered before its initial window loaded"
            raise LayoutInvariantError(message)
        total = visible.total if self.source.countable else None
        offset = cursor.position.offset
        body = (
            (
                Lines(
                    tuple(
                        f"{rank}. **{display_text(label)}** — {display_text(value)}"
                        for rank, entry in enumerate(visible.items, offset + 1)
                        for label, value, _entry_key in (self._row_values(entry),)
                    )
                ),
            )
            if visible.items
            else self._source_hook(self.empty, total, owner, name="empty")
        )
        navigable = visible.has_next or (self.source.bidirectional and visible.has_prev)

        async def previous(_event: ActionEvent) -> None:
            if await cursor.previous():
                owner.sync_position()

        async def next_(_event: ActionEvent) -> None:
            if await cursor.next():
                owner.sync_position()

        pager = (
            actions(
                action(
                    chrome.older,
                    previous,
                    key=f"{self.key}.previous",
                    available=visible.has_prev,
                )
                if self.source.bidirectional
                else None,
                action(chrome.newer, next_, key=f"{self.key}.next", available=visible.has_next),
                key=f"{self.key}.pager",
            )
            if navigable
            else None
        )
        numeric_footer = window_footer(chrome, self.source, cursor.position, visible, cursor.extent)
        return stack(
            heading(self.heading) if self.heading is not None else None,
            *(self._source_hook(self.header, total, owner, name="header") if self.header is not None else ()),
            *body,
            note(numeric_footer) if navigable and numeric_footer is not None else None,
            pager,
            *(self._source_hook(self.footer, total, owner, name="footer") if self.footer is not None else ()),
        )


class _SourceRankedListComponent[EntryT](ComponentShell[RankedListState]):
    cursor: WindowCursor[RankedEntry | EntryT] = state(persist=False, copy="ref")

    def __init__(self, pattern: RankedList[EntryT], *, initial: RankedListState | None = None) -> None:
        super().__init__(pattern, initial=initial)
        assert pattern.source is not None and pattern.page_size is not None
        initial_position = (
            pattern._initial_position if initial is None else Position(offset=max(0, initial.page) * pattern.page_size)
        )
        self.cursor = WindowCursor(pattern.source, pattern.page_size, pattern._identity, initial=initial_position)

    async def on_load(self) -> None:
        await self.cursor.fetch()
        self.sync_position()

    def sync_position(self) -> None:
        pattern = self.pattern
        assert isinstance(pattern, RankedList) and pattern.page_size is not None
        self.pattern_state = RankedListState(self.cursor.position.offset // pattern.page_size)

    def render(self) -> RenderResult:
        pattern = self.pattern
        assert isinstance(pattern, RankedList)
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        return pattern._render_source(self, chrome, self.cursor)
