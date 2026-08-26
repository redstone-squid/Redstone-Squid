"""A pure materialized ranking pattern for component and router shells."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from squid_ui.factories import actions, heading, note, stack
from squid_ui.primitives import Lines
from squid_ui.runtime.component import RenderResult
from squid_ui.semantic import LayoutNode
from squid_ui.sources import ORIGIN, Position
from squid_ui.text import TextLike
from squid_patterns._content import ContentLike, normalize_content, require_key
from squid_patterns._paging import window
from squid_patterns._ranked import Projector, RankedEntry, RankedRows
from squid_patterns.shells import ComponentShell, PatternControls


@dataclass(frozen=True, slots=True)
class RankedListState:
    """Serializable cursor position for :class:`RankedList`."""

    position: Position = ORIGIN


type ContentHook = ContentLike | Callable[[int], ContentLike]


class RankedList[EntryT]:
    """Render a fully materialized ranking through either pattern shell."""

    def __init__(
        self,
        entries: Iterable[RankedEntry | EntryT],
        *,
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
        initial_position: Position = ORIGIN,
    ) -> None:
        self.key = require_key(key, name="RankedList.key")
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
        self.entries = tuple(entries)
        self.rows = RankedRows(label, value, identity)
        self.heading = heading
        self.header = header
        self.footer = footer
        self.page_size = page_size
        self.top_n = top_n if top_n is not None else limit
        self.empty = normalize_content(empty, name="RankedList.empty")
        self._initial_state = RankedListState(initial_position)

    @property
    def initial_state(self) -> RankedListState:
        return self._initial_state

    def build_component(self, *, initial: RankedListState | None = None) -> ComponentShell[RankedListState]:
        """Build the in-memory shell for this ranking."""
        return ComponentShell(self, initial=initial)

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
            return RankedListState(Position(offset=state.position.offset - 1))
        if action == "next":
            return RankedListState(Position(offset=state.position.offset + 1))
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
        displayed = self.entries if self.top_n is None else self.entries[: self.top_n]
        total = len(displayed)
        if self.page_size is None:
            visible, position, pages = displayed, ORIGIN, 1
        else:
            visible, position, pages = window(
                displayed,
                key=self.key,
                position=state.position,
                per_page=self.page_size,
                chrome=controls.chrome,
                identity=self.rows.identity_of,
            )
        page = position.offset
        offset = page * (self.page_size or max(1, total))
        body = (Lines(self.rows.lines(visible, offset)),) if visible else controls.content(self.empty, prefix="empty")
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


__all__ = ["RankedEntry", "RankedList", "RankedListState"]
