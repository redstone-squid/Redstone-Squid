"""HTTP pagination parameters and response models.

Application services assemble pages using `squid.core.pagination`. This module
validates query parameters and converts those pages to JSON representations.
"""

from collections.abc import Callable
from typing import Annotated, Self

from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.pagination import PageAnchor as ResultPageAnchor

PageSizeParam = Annotated[int, QueryParameter(ge=1, le=50, description="Maximum number of items to return.")]
OffsetParam = Annotated[
    int | None,
    QueryParameter(ge=0, le=MAX_PAGE_OFFSET, description="Number of items to skip. Excludes after_id and before_id."),
]
AfterIdParam = Annotated[
    int | None,
    QueryParameter(ge=1, description="Return the items after this identifier in display order. Excludes offset."),
]
BeforeIdParam = Annotated[
    int | None,
    QueryParameter(ge=1, description="Return the items before this identifier in display order. Excludes offset."),
]


class PageAnchor(BaseModel):
    """Query-parameter values addressing an adjacent page. Exactly one field is set."""

    model_config = ConfigDict(extra="forbid")

    offset: int | None = None
    after_id: int | None = None
    before_id: int | None = None

    @classmethod
    def from_domain(cls, value: ResultPageAnchor) -> Self:
        """Serialize one adjacent-page address."""
        return cls(offset=value.offset, after_id=value.after_id, before_id=value.before_id)


class Page[ItemT](BaseModel):
    """One page of resource summaries and the parameters addressing its neighbours."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None

    @classmethod
    def from_domain[ResultT](cls, page: ResultPage[ResultT], render: Callable[[ResultT], ItemT]) -> Self:
        """Serialize an application page and its items as their REST representations."""
        return cls(
            items=[render(item) for item in page.items],
            total=page.total,
            next=PageAnchor.from_domain(page.next) if page.next is not None else None,
            prev=PageAnchor.from_domain(page.prev) if page.prev is not None else None,
        )


def resolve_selector(
    *,
    offset: int | None,
    after_id: int | None = None,
    before_id: int | None = None,
    keyset_allowed: bool = True,
) -> PageSelector:
    """Resolve the mutually exclusive pagination parameters into one selector.

    With no selector, start at offset zero. Supplying more than one selector raises
    ValidationError. Set `keyset_allowed=False` for relevance ranking or another
    ordering that cannot be paged by ID; after_id and before_id then raise too.
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

    Use `default` when no value is supplied. Fields outside `allowed` raise
    ValidationError; callers choose that set to match the ordering they support.
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
