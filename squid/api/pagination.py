"""REST pagination parameters, the wire envelope, and the mapping between them.

The application layer assembles pages (see `squid.core.pagination`); this module owns only what is
HTTP about them -- the query parameters, the rejections they can earn, and the serialized shape.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Query
from pydantic import ConfigDict

from squid.api.schema import ApiSchema
from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.pagination import PageAnchor as ResultPageAnchor

PageSizeParam = Annotated[int, Query(ge=1, le=50, description="Maximum number of items to return.")]
OffsetParam = Annotated[
    int | None,
    Query(ge=0, le=MAX_PAGE_OFFSET, description="Number of items to skip. Excludes after_id and before_id."),
]
AfterIdParam = Annotated[
    int | None,
    Query(ge=1, description="Return the items after this identifier in display order. Excludes offset."),
]
BeforeIdParam = Annotated[
    int | None,
    Query(ge=1, description="Return the items before this identifier in display order. Excludes offset."),
]


class PageAnchor(ApiSchema):
    """Query-parameter values addressing an adjacent page. Exactly one field is set."""

    model_config = ConfigDict(extra="forbid")

    offset: int | None = None
    after_id: int | None = None
    before_id: int | None = None


class Page[ItemT](ApiSchema):
    """One page of resource summaries and the parameters addressing its neighbours."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None


def resolve_selector(
    *,
    offset: int | None,
    after_id: int | None = None,
    before_id: int | None = None,
    keyset_allowed: bool = True,
) -> PageSelector:
    """Resolve the mutually exclusive pagination parameters into one selector.

    Raises `ValidationError` (`INVALID_QUERY`) when more than one is given, or when an id anchor is given
    with `keyset_allowed=False` (an ordering identifiers do not address, such as relevance or another
    column, would page through a different sequence than the anchor was taken from).
    """
    provided = [
        name
        for name, value in (("offset", offset), ("after_id", after_id), ("before_id", before_id))
        if value is not None
    ]
    if len(provided) > 1:
        msg = f"{', '.join(provided)} cannot be combined"
        raise ValidationError(msg, code=ErrorCode.INVALID_QUERY, public_context={"fields": provided})
    if not keyset_allowed and (after_id is not None or before_id is not None):
        msg = "after_id and before_id require ordering by id"
        raise ValidationError(msg, code=ErrorCode.INVALID_QUERY, public_context={"field": "sort"})
    return PageSelector(offset=offset or 0, after_id=after_id, before_id=before_id)


def parse_page_sort(value: str | None, *, allowed: frozenset[str], default: str) -> tuple[str, bool]:
    """Parse a `field` or `-field` sort parameter into a field and its descending flag.

    Raises `ValidationError` (`INVALID_QUERY`) for a field outside `allowed`, so a sort can never name an
    unindexed column and turn a listing into a sequential scan.
    """
    raw = default if value is None else value
    field = raw.removeprefix("-")
    if field not in allowed:
        msg = f"sort field {field!r} is not supported"
        raise ValidationError(
            msg,
            code=ErrorCode.INVALID_QUERY,
            public_context={"field": "sort", "allowed": sorted(allowed)},
        )
    return field, raw.startswith("-")


def anchor(value: ResultPageAnchor | None) -> PageAnchor | None:
    return (
        None if value is None else PageAnchor(offset=value.offset, after_id=value.after_id, before_id=value.before_id)
    )


def render_page[ResultT, ItemT](page: ResultPage[ResultT], render: Callable[[ResultT], ItemT]) -> Page[ItemT]:
    return Page[ItemT](
        items=[render(item) for item in page.items],
        total=page.total,
        next=anchor(page.next),
        prev=anchor(page.prev),
    )
