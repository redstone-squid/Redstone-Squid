"""Search requests and results, built the same way by the bot and the API."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET
from squid.core.search.domain.query import BuildQuery, RecordQuery
from squid.core.search.domain.scope import SearchScope as SearchScope
from squid.core.validation import (
    AboveMaximum,
    AboveMaximumParams,
    BelowMinimum,
    BelowMinimumParams,
    FieldErrorCode,
    SimpleIssue,
)


class SearchMode(StrEnum):
    """Candidate ranking strategy."""

    LEXICAL = "lexical"
    SEMANTIC = "semantic"


class SortDirection(StrEnum):
    """Direction for an explicit search-field sort."""

    ASCENDING = "asc"
    DESCENDING = "desc"


@dataclass(frozen=True, slots=True)
class SearchSort:
    """A field and direction used to sort search results."""

    field: str
    direction: SortDirection = SortDirection.ASCENDING

    @classmethod
    def parse(cls, value: str | None) -> SearchSort | None:
        """Parse `field` as ascending and `-field` as descending.

        None selects the default ordering. Empty field names raise ValidationError;
        whether a named field supports sorting is checked by SearchService.
        """
        if value is None:
            return None
        direction = SortDirection.DESCENDING if value.startswith("-") else SortDirection.ASCENDING
        field = value.removeprefix("-")
        if not field:
            msg = "sort field is required"
            raise ValidationError(
                msg,
                code=ErrorCode.INVALID_QUERY,
                issues=(SimpleIssue(field="/query/sort", code=FieldErrorCode.REQUIRED),),
            )
        return cls(field, direction)


@dataclass(frozen=True, slots=True)
class SearchResultAnchor:
    """Find the page containing a build or record by its indexed source ID."""

    resource_kind: Literal["build", "record"]
    source_id: str


type HistogramField = Literal["width", "opening_time"]


@dataclass(frozen=True, slots=True)
class SearchRange:
    field: HistogramField
    lower: int | None
    upper: int | None

    def __post_init__(self) -> None:
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            msg = f"{self.field} minimum exceeds maximum"
            raise ValidationError(msg, code=ErrorCode.INVALID_QUERY)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Search text, filters, and paging options shared by the bot and API."""

    query: str
    scope: SearchScope = SearchScope.RECORDS
    mode: SearchMode = SearchMode.LEXICAL
    page_size: int = 5
    offset: int = 0
    sort: SearchSort | None = None
    visible_statuses: frozenset[str] | None = None
    group_by_build: bool = False
    parsed_query: BuildQuery | RecordQuery | None = None
    anchor: SearchResultAnchor | None = None
    ranges: tuple[SearchRange, ...] = ()

    def __post_init__(self) -> None:
        if self.parsed_query is not None and self.parsed_query.scope is not self.scope:
            msg = "parsed query scope must match the search request"
            raise ValidationError(msg, code=ErrorCode.INVALID_QUERY)
        if not 1 <= self.page_size <= 50:
            msg = "page_size must be between 1 and 50"
            raise ValidationError(
                msg,
                issues=(
                    BelowMinimum(field="page_size", params=BelowMinimumParams(minimum=1))
                    if self.page_size < 1
                    else AboveMaximum(field="page_size", params=AboveMaximumParams(maximum=50)),
                ),
            )
        # The REST layer rejects out-of-range offsets as 422 before reaching here; this guards the
        # Discord bot and any other in-process caller.
        if not 0 <= self.offset <= MAX_PAGE_OFFSET:
            msg = f"offset must be between 0 and {MAX_PAGE_OFFSET}"
            raise ValidationError(
                msg,
                issues=(
                    BelowMinimum(field="offset", params=BelowMinimumParams(minimum=0))
                    if self.offset < 0
                    else AboveMaximum(field="offset", params=AboveMaximumParams(maximum=MAX_PAGE_OFFSET)),
                ),
            )


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """A contributing document and its ranking branches, if relevance was used."""

    resource_kind: Literal["build", "record", "metadata"]
    source_id: str
    branches: tuple[str, ...] = ()
