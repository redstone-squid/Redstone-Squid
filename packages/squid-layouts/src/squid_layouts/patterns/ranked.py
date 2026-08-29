"""A paginated ranked collection with global rank numbering."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from squid_layouts.factories import bullet, bullets, heading, stack
from squid_layouts.patterns._content import ContentLike, display_text, normalize_content, render_content, require_key
from squid_layouts.runtime.component import Component
from squid_layouts.semantic import LayoutNode, ListItem
from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True)
class RankedEntry:
    """An already-ranked row for callers that do not need projection callbacks."""

    label: TextLike
    value: object
    key: str = ""


type Projector[EntryT] = str | Callable[[EntryT], object]
type ContentHook = ContentLike | Callable[[int], ContentLike]


class RankedList[EntryT](Component):
    """Render an ordered collection with ranks that remain correct across pages.

    Input order is preserved: ranking belongs to the caller or data source. ``top_n`` limits
    the materialized display, while ``page_size`` is passed to semantic ``List`` so the
    planner adds measured pagination controls and footers without this pattern doing target
    arithmetic.

    ``label`` and ``value`` may be callables or attribute/mapping keys. Two-tuples are also
    accepted without projectors. A ``header`` or ``footer`` hook receives the displayed row
    count and may return any normal semantic content.
    """

    def __init__(
        self,
        entries: Iterable[RankedEntry | EntryT],
        *,
        key: str,
        label: Projector[EntryT] | None = None,
        value: Projector[EntryT] | None = None,
        heading: TextLike | None = None,
        header: ContentHook | None = None,
        footer: ContentHook | None = None,
        page_size: int | None = None,
        top_n: int | None = None,
        limit: int | None = None,
        empty: ContentLike = "No entries",
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
        self.label = label
        self.value = value
        self.heading = heading
        self.header = header
        self.footer = footer
        self.page_size = page_size
        self.top_n = top_n if top_n is not None else limit
        self.empty = normalize_content(empty, name="RankedList.empty")

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

    def _hook(self, hook: ContentHook, total: int, *, name: str) -> tuple[LayoutNode, ...]:
        value = hook(total) if callable(hook) else hook
        return render_content(self, normalize_content(value, name=name), prefix=name)

    def render(self) -> LayoutNode:
        displayed = self.entries if self.top_n is None else self.entries[: self.top_n]
        total = len(displayed)
        header = self._hook(self.header, total, name="header") if self.header is not None else ()
        if displayed:
            rows: tuple[ListItem, ...] = tuple(
                bullet(
                    f"**{display_text(label)}** — {display_text(value)}",
                    key=entry_key or f"rank-{rank}",
                )
                for rank, entry in enumerate(displayed, 1)
                for label, value, entry_key in (self._row_values(entry),)
            )
            body = (bullets(*rows, key=self.key, ordered=True, page_size=self.page_size),)
        else:
            body = render_content(self, self.empty, prefix="empty")
        footer = self._hook(self.footer, total, name="footer") if self.footer is not None else ()
        return stack(
            heading(self.heading) if self.heading is not None else None,
            *header,
            *body,
            *footer,
        )
