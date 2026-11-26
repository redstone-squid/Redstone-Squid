"""Search requests and results, built the same way by the bot and the API."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageAnchor
from squid.core.search.domain.evidence import SearchEvidence
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


@dataclass(frozen=True, slots=True)
class RecordSearchHolder:
    """A current holder and the public credits for its build."""

    build_id: int
    title: str
    creators: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecordSearchHit:
    """A record search result. `source_id` is `record:<public ID>`."""

    source_id: str
    title: str
    subtitle: str | None
    build_id: int
    build_title: str
    criterion: str
    version_scope: str
    score: float | None = None
    metrics: dict[str, str | int | float | bool] = field(default_factory=dict)
    holder_build_ids: tuple[int, ...] = ()
    holders: tuple[RecordSearchHolder, ...] = ()
    matched_holder_id: int | None = None
    matched: bool = True
    evidence: SearchEvidence = field(default_factory=SearchEvidence)
    """The values that made this hit match."""
    retired: bool = False
    """A retired record has no competitors and no holders; only a `record_state` query finds it."""
    resource_kind: Literal["record"] = field(default="record", init=False)

    @property
    def one_line_title(self) -> str:
        """The title and subtitle on one line; many records share a title and differ only by subtitle."""
        return f"{self.title} · {self.subtitle}" if self.subtitle else self.title


@dataclass(frozen=True, slots=True)
class BuildSearchHit:
    """A build search result."""

    source_id: str
    title: str
    status: str
    description: str | None = None
    score: float | None = None
    tags: tuple[str, ...] = ()
    records: tuple[RecordSearchHit, ...] = ()
    matches: tuple[SearchMatch, ...] = ()
    evidence: SearchEvidence = field(default_factory=SearchEvidence)
    """The values that made this hit match."""
    dimensions: tuple[int | None, int | None, int | None] = (None, None, None)
    """Width, height, and depth in blocks; None marks an unknown dimension."""
    resource_kind: Literal["build"] = field(default="build", init=False)


@dataclass(frozen=True, slots=True)
class MetadataSearchHit:
    """A tag or version search result."""

    source_id: str
    title: str
    metadata_kind: str
    description: str | None = None
    score: float | None = None
    aliases: tuple[str, ...] = ()
    matches: tuple[SearchMatch, ...] = ()
    evidence: SearchEvidence = field(default_factory=SearchEvidence)
    """The values that made this hit match."""
    resource_kind: Literal["metadata"] = field(default="metadata", init=False)


type SearchHit = RecordSearchHit | BuildSearchHit | MetadataSearchHit


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One page of search results with offset-based navigation.

    Relevance order does not follow resource IDs, so neighbouring pages use offsets.
    For ranked searches, total counts the collected candidates, which may be fewer
    than all documents matching the query.
    """

    hits: tuple[SearchHit, ...]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None
    warnings: tuple[str, ...] = ()
    offset: int | None = None
