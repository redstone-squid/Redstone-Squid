"""A pure materialized ranking machine for component and router shells."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from squid_ui.document import DocumentLike
from squid_ui.factories import action_controls, heading, note, stack
from squid_ui.primitives import Lines
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import DiscordTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets._paging import FIRST_PAGE, PagePosition, window
from squid_ui_widgets._ranked import Projector, RankedEntry, RankedRows
from squid_ui_widgets.drivers import ComponentDriver, MachineControls


@dataclass(frozen=True, slots=True)
class RankedListState:
    """Serializable page position for :class:`RankedList`."""

    page: PagePosition = FIRST_PAGE


type ContentHook[RenderTargetT: DiscordTarget = DiscordTarget] = (
    ContentLike[RenderTargetT] | Callable[[int], ContentLike[RenderTargetT]]
)


class RankedList[EntryT, RenderTargetT: DiscordTarget = DiscordTarget]:
    """Render a fully materialized ranking through either machine shell."""

    def __init__(
        self,
        entries: Iterable[RankedEntry | EntryT],
        *,
        key: str,
        label: Projector[EntryT] | None = None,
        value: Projector[EntryT] | None = None,
        identity: Projector[EntryT] | None = None,
        title: TextLike | None = None,
        header: ContentHook[RenderTargetT] | None = None,
        footer: ContentHook[RenderTargetT] | None = None,
        page_size: int | None = None,
        top_n: int | None = None,
        empty: ContentLike[RenderTargetT] = "No entries",
        initial_page: PagePosition = FIRST_PAGE,
    ) -> None:
        self.key = require_key(key, name="RankedList.key")
        if page_size is not None and page_size < 1:
            message = "RankedList.page_size must be at least 1"
            raise ValueError(message)
        if top_n is not None and top_n < 1:
            message = "RankedList.top_n must be at least 1"
            raise ValueError(message)
        self.entries = tuple(entries)
        self.rows = RankedRows(label, value, identity)
        self.title = title
        self.header = header
        self.footer = footer
        self.page_size = page_size
        self.top_n = top_n
        self.empty = normalize_content(empty, name="RankedList.empty")
        self._initial_state = RankedListState(initial_page)

    @property
    def initial_state(self) -> RankedListState:
        return self._initial_state

    def build_component(
        self, *, initial: RankedListState | None = None
    ) -> ComponentDriver[RankedListState, RenderTargetT]:
        """Build the in-memory shell for this ranking."""
        if initial is None:
            return ComponentDriver(self)
        return ComponentDriver(self, initial=initial)

    def transition(
        self,
        state: RankedListState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> RankedListState:
        del values, submitted
        last_page = self._page_count() - 1
        if action == "previous":
            return RankedListState(PagePosition(max(0, state.page.index - 1)))
        if action == "next":
            return RankedListState(PagePosition(min(last_page, state.page.index + 1)))
        return state

    def _page_count(self) -> int:
        total = len(self.entries) if self.top_n is None else min(len(self.entries), self.top_n)
        return 1 if self.page_size is None else max(1, (total + self.page_size - 1) // self.page_size)

    def _hook(
        self,
        hook: ContentHook[RenderTargetT],
        total: int,
        controls: MachineControls[RankedListState, RenderTargetT],
        *,
        name: str,
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        value = hook(total) if callable(hook) else hook
        return controls.content(normalize_content(value, name=name), prefix=name)

    def render(
        self, state: RankedListState, controls: MachineControls[RankedListState, RenderTargetT]
    ) -> DocumentLike[RenderTargetT]:
        displayed = self.entries if self.top_n is None else self.entries[: self.top_n]
        total = len(displayed)
        if self.page_size is None:
            visible, position, pages = displayed, FIRST_PAGE, 1
        else:
            visible, position, pages = window(
                displayed,
                key=self.key,
                position=state.page,
                per_page=self.page_size,
                chrome=controls.chrome,
                identity=self.rows.identity_of,
            )
        page = position.index
        offset = page * (self.page_size or max(1, total))
        body = (Lines(self.rows.lines(visible, offset)),) if visible else controls.content(self.empty, prefix="empty")
        pager = (
            action_controls(
                controls.action_control(
                    controls.chrome.previous,
                    "previous",
                    key=f"{self.key}.previous",
                    available=page > 0,
                ),
                controls.action_control(
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
            heading(self.title) if self.title is not None else None,
            *(self._hook(self.header, total, controls, name="header") if self.header is not None else ()),
            *body,
            note(controls.chrome.page_footer(page + 1, pages)) if pages > 1 else None,
            pager,
            *(self._hook(self.footer, total, controls, name="footer") if self.footer is not None else ()),
        )


__all__ = ["PagePosition", "RankedEntry", "RankedList", "RankedListState"]
