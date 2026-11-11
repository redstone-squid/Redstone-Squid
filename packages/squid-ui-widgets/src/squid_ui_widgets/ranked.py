"""A pure materialized ranking machine for component and router shells."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from squid_ui.document import DocumentLike
from squid_ui.factories import heading, stack
from squid_ui.primitives import Lines
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import DiscordTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets._ranked import Projector, RankedEntry, RankedRows
from squid_ui_widgets.drivers import ComponentDriver, FormValues, MachineControls


@dataclass(frozen=True, slots=True)
class RankedListState:
    """Serializable zero-based page for :class:`RankedList`."""

    page: int = 0

    def __post_init__(self) -> None:
        if self.page < 0:
            message = "RankedListState.page must not be negative"
            raise ValueError(message)


type ContentHook[RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object] = (
    ContentLike[RenderTargetT, ContextT] | Callable[[int], ContentLike[RenderTargetT, ContextT]]
)


class RankedList[EntryT, RenderTargetT: DiscordTarget = DiscordTarget, ContextT = object]:
    """Page a snapshot of entries in their supplied ranking order.

    Entries are not sorted. top_n caps the snapshot before paging, and page_size=None
    shows it all. Header and footer callbacks receive the count after the cap. Rows are
    numbered across the whole ranking, so the first row on page two of a ten-row page
    is `11.`.
    """

    def __init__(
        self,
        entries: Iterable[RankedEntry | EntryT],
        *,
        key: str,
        label: Projector[EntryT] | None = None,
        value: Projector[EntryT] | None = None,
        title: TextLike | None = None,
        header: ContentHook[RenderTargetT, ContextT] | None = None,
        footer: ContentHook[RenderTargetT, ContextT] | None = None,
        page_size: int | None = None,
        top_n: int | None = None,
        empty: ContentLike[RenderTargetT, ContextT] = "No entries",
        initial_page: int = 0,
    ) -> None:
        self.key = require_key(key, name="RankedList.key")
        if page_size is not None and page_size < 1:
            message = "RankedList.page_size must be at least 1"
            raise ValueError(message)
        if top_n is not None and top_n < 1:
            message = "RankedList.top_n must be at least 1"
            raise ValueError(message)
        self.entries = tuple(entries)
        # The planner pager fingerprints the rendered lines, so rows need no separate identity.
        self.rows = RankedRows(label, value, None)
        self.title = title
        self.header = header
        self.footer = footer
        self.page_size = page_size
        self.top_n = top_n
        self.empty = normalize_content(empty, name="RankedList.empty")
        self._initial_state = RankedListState(initial_page)

    @property
    def initial_state(self) -> RankedListState:
        """Return the configured initial page."""
        return self._initial_state

    def build_component(
        self, *, initial: RankedListState | None = None
    ) -> ComponentDriver[RankedListState, RenderTargetT, ContextT]:
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
        submitted: FormValues | None = None,
    ) -> RankedListState:
        """Store the page a pager asked for; the pager clamps it to the pages that exist."""
        del submitted
        if action == "page" and values and values[0].isdigit():
            return RankedListState(int(values[0]))
        return state

    def _hook(
        self,
        hook: ContentHook[RenderTargetT, ContextT],
        total: int,
        controls: MachineControls[RankedListState, RenderTargetT, ContextT],
        *,
        name: str,
    ) -> tuple[LayoutNode[RenderTargetT, ContextT], ...]:
        """Resolve and normalize header or footer content."""
        value = hook(total) if callable(hook) else hook
        return controls.content(normalize_content(value, name=name))

    def render(
        self, state: RankedListState, controls: MachineControls[RankedListState, RenderTargetT, ContextT]
    ) -> DocumentLike[RenderTargetT, ContextT]:
        """Render the ranking, leaving the page slice to the driver's pager."""
        displayed = self.entries if self.top_n is None else self.entries[: self.top_n]
        total = len(displayed)
        lines = self.rows.lines(displayed, 0)
        if not displayed:
            body = controls.content(self.empty)
        elif self.page_size is None:
            body = (Lines(lines),)
        else:
            body = controls.paged_lines(lines, "page", key=self.key, page=state.page, per=self.page_size)
        return stack(
            heading(self.title) if self.title is not None else None,
            *(self._hook(self.header, total, controls, name="header") if self.header is not None else ()),
            *body,
            *(self._hook(self.footer, total, controls, name="footer") if self.footer is not None else ()),
        )


__all__ = ["RankedEntry", "RankedList", "RankedListState"]
