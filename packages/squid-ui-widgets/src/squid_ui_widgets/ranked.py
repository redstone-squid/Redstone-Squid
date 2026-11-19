"""Ranked row projection and the state machine for materialized rankings."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from squid_ui.authoring.document import DocumentLike
from squid_ui.authoring.semantic import LayoutNode, List, ListItem
from squid_ui.content.text import Text, TextLike, md
from squid_ui.factories import heading, stack
from squid_ui.target_types import RenderTarget
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets.drivers import ComponentDriver, FormValues, MachineControls


@dataclass(frozen=True, slots=True)
class RankedEntry:
    """An already-ranked row for callers that do not need projection callbacks."""

    label: TextLike
    value: object
    key: str = ""


type Projector[EntryT] = str | Callable[[EntryT], object]


class RankedRows[EntryT]:
    """Project arbitrary entries into stable ranked display rows."""

    def __init__(
        self,
        label: Projector[EntryT] | None,
        value: Projector[EntryT] | None,
        identity: Projector[EntryT] | None,
    ) -> None:
        self.label = label
        self.value = value
        self.identity = identity

    @staticmethod
    def project(entry: EntryT, projector: Projector[EntryT]) -> object:
        """Apply a callback, mapping key, or attribute projector."""
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

    def values(self, entry: RankedEntry | EntryT) -> tuple[object, object]:
        """Return the display label and value for one entry."""
        if isinstance(entry, RankedEntry):
            if self.label is not None or self.value is not None:
                message = "projectors cannot be combined with RankedEntry values"
                raise TypeError(message)
            return entry.label, entry.value
        if self.label is None or self.value is None:
            if isinstance(entry, tuple) and len(entry) == 2:
                return entry[0], entry[1]
            message = "ranking needs label and value projectors for non-tuple entries"
            raise TypeError(message)
        return self.project(entry, self.label), self.project(entry, self.value)

    def identity_of(self, entry: RankedEntry | EntryT) -> str:
        """Return the entry identity used to anchor paging."""
        if isinstance(entry, RankedEntry) and entry.key:
            return entry.key
        if self.identity is not None and not isinstance(entry, RankedEntry):
            return str(self.project(entry, self.identity))
        return repr(entry)

    def lines(self, entries: tuple[RankedEntry | EntryT, ...], offset: int) -> tuple[TextLike, ...]:
        """Format entries with ranks continuing after `offset`."""
        return tuple(
            md(t"{rank}. **{label}** — {value}")
            for rank, entry in enumerate(entries, offset + 1)
            for label, value in (self.values(entry),)
        )


@dataclass(frozen=True, slots=True)
class RankedListState:
    """Serializable zero-based page for :class:`RankedList`."""

    page: int = 0

    def __post_init__(self) -> None:
        if self.page < 0:
            message = "RankedListState.page must not be negative"
            raise ValueError(message)


type ContentHook[RenderTargetT: RenderTarget = RenderTarget, ContextT = object] = (
    ContentLike[RenderTargetT, ContextT] | Callable[[int], ContentLike[RenderTargetT, ContextT]]
)


class RankedList[EntryT, RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
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
        title = None if title is None else Text(title)
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
            body = (
                List(
                    tuple(ListItem(str(index), line) for index, line in enumerate(lines)),
                    key=f"{self.key}.rows",
                    markers=False,
                ),
            )
        else:
            body = controls.paged_lines(lines, "page", key=self.key, page=state.page, per=self.page_size)
        return stack(
            heading(self.title) if self.title is not None else None,
            *(self._hook(self.header, total, controls, name="header") if self.header is not None else ()),
            *body,
            *(self._hook(self.footer, total, controls, name="footer") if self.footer is not None else ()),
        )


__all__ = ["RankedEntry", "RankedList", "RankedListState"]
