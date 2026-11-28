"""HTTP pagination parameters and response models.

Application services assemble pages using `squid.core.pagination`. This module
validates query parameters and converts those pages to JSON representations.
"""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Any, Self, cast, overload, override

from litestar import Litestar, Request, Response
from litestar.params import QueryParameter
from litestar.response.base import ASGIResponse
from pydantic import BaseModel, ConfigDict

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageCursor, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.pagination import PageAnchor as ResultPageAnchor
from squid.core.validation import (
    FieldErrorCode,
    SimpleIssue,
)

PageSizeParam = Annotated[int, QueryParameter(ge=1, le=50, description="Maximum number of items to return.")]
OffsetParam = Annotated[
    int | None,
    QueryParameter(ge=0, le=MAX_PAGE_OFFSET, description="Number of items to skip. Excludes cursor."),
]
CursorParam = Annotated[
    str | None,
    QueryParameter(
        description="An opaque cursor from a page's `next` or `prev`. Send it with the same sort. Excludes offset.",
    ),
]


class PageAnchor(BaseModel):
    """Query-parameter values addressing an adjacent page. Exactly one field is set.

    Merge them into the next request unchanged; cursors are opaque.
    """

    model_config = ConfigDict(extra="forbid")

    offset: int | None = None
    cursor: str | None = None

    @classmethod
    def from_domain(cls, value: ResultPageAnchor) -> Self:
        """Serialize one adjacent-page address."""
        return cls(offset=value.offset, cursor=None if value.cursor is None else value.cursor.encode())


@dataclass
class Page[ItemT]:
    """One page of resource summaries and the parameters addressing its neighbours.

    DTOs replace items during serialization, so this HTTP envelope is mutable.
    Application pages are copied before serialization and remain unchanged.
    """

    items: list[ItemT]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None

    @classmethod
    @overload
    def from_domain(cls, page: ResultPage[ItemT]) -> Self: ...

    @classmethod
    @overload
    def from_domain[ResultT](cls, page: ResultPage[ResultT], render: Callable[[ResultT], ItemT]) -> Self: ...

    @classmethod
    def from_domain[ResultT](cls, page: ResultPage[ResultT], render: Callable[[ResultT], ItemT] | None = None) -> Self:
        """Copy an application page, optionally mapping items that need explicit assembly."""
        # The overload without a renderer requires matching item types.
        items = [render(item) for item in page.items] if render is not None else cast(list[ItemT], list(page.items))
        return cls(
            items=items,
            total=page.total,
            next=PageAnchor.from_domain(page.next) if page.next is not None else None,
            prev=PageAnchor.from_domain(page.prev) if page.prev is not None else None,
        )


def resolve_selector(*, offset: int | None, cursor: str | None = None) -> PageSelector:
    """Resolve the mutually exclusive `offset` and `cursor` parameters into one selector.

    With neither, seekable lists start in cursor mode.

    Raises:
        ValidationError: Both were supplied.
        InvalidPageCursorError: The cursor is malformed.
    """
    if offset is not None and cursor is not None:
        msg = "offset and cursor cannot be combined"
        raise ValidationError(
            msg,
            code=ErrorCode.INVALID_QUERY,
            issues=tuple(
                SimpleIssue(field=f"/query/{key}", code=FieldErrorCode.INCOMPATIBLE_FIELDS)
                for key in ("offset", "cursor")
            ),
        )
    return PageSelector(offset=offset, cursor=None if cursor is None else PageCursor.decode(cursor))


class ListResponse[T](Response[T]):
    """Revalidate public pages against their serialized representation."""

    @override
    def to_asgi_response(self, app: Litestar | None, request: Request, **kwargs: Any) -> ASGIResponse:
        response = super().to_asgi_response(app, request, **kwargs)
        if "no-store" in response.headers.get("cache-control", "") or "Squid-Impersonation-Session" in request.headers:
            return response
        etag = f'"{sha256(response.body).hexdigest()}"'
        response.headers["etag"] = etag
        if any(
            tag.strip().removeprefix("W/") in {etag, "*"} for tag in request.headers.get("if-none-match", "").split(",")
        ):
            response.status_code = 304
            response.body = b""
            del response.headers["content-length"]
        return response
