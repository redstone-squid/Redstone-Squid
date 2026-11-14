"""Search requests and results, built the same way by the bot and the API."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import MAX_PAGE_OFFSET, PageAnchor


class SearchScope(StrEnum):
    """Resources included in a search."""

    RECORDS = "records"
    BUILDS = "builds"
    METADATA = "metadata"
    ALL = "all"


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
            raise ValidationError(msg, code=ErrorCode.INVALID_QUERY)
        return cls(field, direction)


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

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 50:
            msg = "page_size must be between 1 and 50"
            raise ValidationError(msg, public_context={"field": "page_size", "minimum": 1, "maximum": 50})
        # The REST layer rejects out-of-range offsets as 422 before reaching here; this guards the
        # Discord bot and any other in-process caller.
        if not 0 <= self.offset <= MAX_PAGE_OFFSET:
            msg = f"offset must be between 0 and {MAX_PAGE_OFFSET}"
            raise ValidationError(msg, public_context={"field": "offset", "minimum": 0, "maximum": MAX_PAGE_OFFSET})


@dataclass(frozen=True, slots=True)
class SearchText:
    """Display text with best-effort literal spans, using Python character offsets.

    Spans are half-open and refer to this exact text, not a subsequently hydrated
    title. An empty tuple means no reliable word-level highlight is available.
    """

    text: str
    spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """A contributing document and its ranking branches, if relevance was used."""

    resource_kind: Literal["build", "record", "metadata"]
    source_id: str
    branches: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecordSearchHit:
    """A computed record search result."""

    source_id: str
    title: str
    subtitle: str | None
    build_id: int
    build_title: str
    base_title: str
    version_scope: str
    score: float | None = None
    tags: tuple[str, ...] = ()
    metrics: dict[str, str | int | float | bool] = field(default_factory=dict)
    holder_build_ids: tuple[int, ...] = ()
    matched: bool = True
    title_match: SearchText | None = None
    resource_kind: Literal["record"] = field(default="record", init=False)


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
    title_match: SearchText | None = None
    resource_kind: Literal["build"] = field(default="build", init=False)


@dataclass(frozen=True, slots=True)
class MetadataSearchHit:
    """A taxonomy or version search result."""

    source_id: str
    title: str
    metadata_kind: str
    description: str | None = None
    score: float | None = None
    aliases: tuple[str, ...] = ()
    matches: tuple[SearchMatch, ...] = ()
    title_match: SearchText | None = None
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
