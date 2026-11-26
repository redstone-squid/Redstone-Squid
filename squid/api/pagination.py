"""HTTP pagination parameters and response models.

Application services assemble pages using `squid.core.pagination`. This module
validates query parameters and converts those pages to JSON representations.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Self, cast, overload
from uuid import UUID

from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageSelector
from squid.core.pagination import Page as ResultPage
from squid.core.pagination import PageAnchor as ResultPageAnchor
from squid.core.validation import (
    FieldErrorCode,
    SimpleIssue,
)

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
UuidAfterIdParam = Annotated[
    UUID | None,
    QueryParameter(description="Return the items after this identifier in display order. Excludes offset."),
]
UuidBeforeIdParam = Annotated[
    UUID | None,
    QueryParameter(description="Return the items before this identifier in display order. Excludes offset."),
]


class PageAnchor(BaseModel):
    """Query-parameter values addressing an adjacent page. Exactly one field is set.

    IDs are integers, or UUIDs on lists keyed by UUID; send them back unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    offset: int | None = None
    after_id: int | UUID | None = None
    before_id: int | UUID | None = None

    @classmethod
    def from_domain(cls, value: ResultPageAnchor[Any]) -> Self:
        """Serialize one adjacent-page address."""
        return cls(offset=value.offset, after_id=value.after_id, before_id=value.before_id)


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
    def from_domain(cls, page: ResultPage[ItemT, Any]) -> Self: ...

    @classmethod
    @overload
    def from_domain[ResultT](cls, page: ResultPage[ResultT, Any], render: Callable[[ResultT], ItemT]) -> Self: ...

    @classmethod
    def from_domain[ResultT](
        cls, page: ResultPage[ResultT, Any], render: Callable[[ResultT], ItemT] | None = None
    ) -> Self:
        """Copy an application page, optionally mapping items that need explicit assembly."""
        # The overload without a renderer requires matching item types.
        items = [render(item) for item in page.items] if render is not None else cast(list[ItemT], list(page.items))
        return cls(
            items=items,
            total=page.total,
            next=PageAnchor.from_domain(page.next) if page.next is not None else None,
            prev=PageAnchor.from_domain(page.prev) if page.prev is not None else None,
        )


def resolve_selector[KeyT: int | UUID = int](
    *,
    offset: int | None,
    after_id: KeyT | None = None,
    before_id: KeyT | None = None,
) -> PageSelector[KeyT]:
    """Resolve the mutually exclusive pagination parameters into one selector.

    With no selector, start at offset zero. Supplying more than one selector raises
    ValidationError.
    """
    provided = [
        name
        for name, value in (("offset", offset), ("after_id", after_id), ("before_id", before_id))
        if value is not None
    ]
    if len(provided) > 1:
        msg = f"{', '.join(provided)} cannot be combined"
        raise ValidationError(
            msg,
            code=ErrorCode.INVALID_QUERY,
            issues=tuple(
                SimpleIssue(field=f"/query/{key}", code=FieldErrorCode.INCOMPATIBLE_FIELDS) for key in provided
            ),
        )
    return PageSelector(offset=offset or 0, after_id=after_id, before_id=before_id)
