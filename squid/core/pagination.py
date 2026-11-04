"""Page contents and the parameters used to request neighbouring pages.

Services query the data and assemble pages; transports expose the anchors in
their own response format. Identifier anchors support full traversal without
the offset limit used for deep numbered pages.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

MAX_PAGE_OFFSET = 10_000
"""Maximum offset accepted by paginated endpoints.

Use identifier anchors for deeper traversal without scanning past every row
before the requested offset. The assembly helpers here do not enforce this limit.
"""


@dataclass(frozen=True, slots=True)
class PageAnchor:
    """An adjacent page address. Callers should set exactly one field."""

    offset: int | None = None
    after_id: int | None = None
    before_id: int | None = None


@dataclass(frozen=True, slots=True)
class PageSelector:
    """The single resolved way one request addresses its page."""

    offset: int = 0
    after_id: int | None = None
    before_id: int | None = None


@dataclass(frozen=True, slots=True)
class Page[ItemT]:
    """One page of results and the parameters addressing its neighbours."""

    items: tuple[ItemT, ...]
    # TODO: If profiling shows count queries are costly, make totals optional for callers that only need navigation.
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None


FIRST_PAGE = PageSelector()
"""The selector a request that named no pagination parameter resolves to."""


def offset_page[ItemT](rows: Sequence[ItemT], *, offset: int, page_size: int) -> Page[ItemT]:
    """Assemble one offset-addressed page from a collection already held in full."""
    total = len(rows)
    return Page(
        items=tuple(rows[offset : offset + page_size]),
        total=total,
        next=PageAnchor(offset=offset + page_size) if offset + page_size < total else None,
        prev=PageAnchor(offset=max(offset - page_size, 0)) if offset else None,
    )


def keyset_page[ItemT](
    rows: Sequence[ItemT],
    *,
    selector: PageSelector,
    page_size: int,
    total: int,
    keyset: bool,
    id_of: Callable[[ItemT], int],
) -> Page[ItemT]:
    """Assemble a page from at most `page_size + 1` rows in display order.

    The extra row indicates another page in the direction of travel. Backward
    queries must reverse their results into display order before calling this
    helper; their extra row is removed from the front. Forward queries lose the
    extra row at the end.

    Set `keyset` only when identifier anchors match the display order. A nonzero
    offset or `keyset=False` produces offset anchors. With `keyset=True`, a zero
    offset is treated as the first identifier-based page.
    """
    overflow = len(rows) > page_size
    backward = selector.before_id is not None
    selected = tuple(rows[-page_size:] if backward else rows[:page_size]) if overflow else tuple(rows)
    if not keyset or selector.offset:
        return Page(
            items=selected,
            total=total,
            next=PageAnchor(offset=selector.offset + page_size) if selector.offset + page_size < total else None,
            prev=PageAnchor(offset=max(selector.offset - page_size, 0)) if selector.offset else None,
        )
    if not selected:
        return Page(items=selected, total=total, next=None, prev=None)
    if backward:
        # Arriving from a later page: there is always something ahead to go back to.
        return Page(
            items=selected,
            total=total,
            next=PageAnchor(after_id=id_of(selected[-1])),
            prev=PageAnchor(before_id=id_of(selected[0])) if overflow else None,
        )
    return Page(
        items=selected,
        total=total,
        next=PageAnchor(after_id=id_of(selected[-1])) if overflow else None,
        prev=PageAnchor(before_id=id_of(selected[0])) if selector.after_id is not None else None,
    )
