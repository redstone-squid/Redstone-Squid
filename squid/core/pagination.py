"""Transport-neutral pagination: how a page is addressed, and how one is assembled.

Pagination is transparent: every value a caller needs to reach an adjacent page is a plain
identifier or offset it could also have supplied by hand. A page addresses its neighbours in the
mode the request used -- an offset-addressed request gets an offset back, and a request in an
identifier order gets `after_id` / `before_id`, including the parameterless first page. That
distinction is not cosmetic: identifier anchors walk a collection of any size, so a full crawl
never runs into `MAX_PAGE_OFFSET`.

Application services own page assembly for the collections they query, because the overfetch and
the total are theirs. Transports map the result onto their own wire format.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

MAX_PAGE_OFFSET = 10_000
"""Deepest offset any paginated collection will serve.

The corpus is ~10^4-10^5 rows, so an `OFFSET 10000` scan over an indexed table costs single-digit
milliseconds; past that, offset paging stops being the right tool. Full traversal of a collection
is still unbounded through the `after_id` keyset chain, which no clamp applies to.
"""


@dataclass(frozen=True, slots=True)
class PageAnchor:
    """The values addressing one adjacent page. Exactly one field is set."""

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
    """Assemble one page from a `page_size + 1` overfetch already in display order.

    The extra row is the evidence that another page exists in the direction of travel, so it is
    trimmed from whichever end the query was walking towards: the front for a backward page, which
    was fetched away from its anchor and flipped back, and the tail otherwise.

    `keyset` says whether the display order is the one identifier anchors address. When it is not
    -- a sort on another column -- or when the caller addressed this page by offset, the neighbours
    are addressed by offset too, so a caller never receives an anchor it cannot use.
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
