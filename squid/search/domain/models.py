"""Search requests and results, built the same way by the bot and the API."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, TypeAlias

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
    """A caller-selected scalar field ordering."""

    field: str
    direction: SortDirection = SortDirection.ASCENDING

    @classmethod
    def parse(cls, value: str | None) -> SearchSort | None:
        """Parse a `-field` or `field` sort expression, or `None` for the default.

        The syntax belongs to the domain, not to one transport: the HTTP routes
        and the bot's search command have to agree on what `-submission_time`
        means, and they can only do that by parsing it in one place.
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
    """A normalized search request passed to an application service."""

    query: str
    scope: SearchScope = SearchScope.RECORDS
    mode: SearchMode = SearchMode.LEXICAL
    page_size: int = 5
    offset: int = 0
    sort: SearchSort | None = None
    visible_statuses: frozenset[str] | None = None

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
class RecordSearchHit:
    """A computed record search result."""

    source_id: str
    title: str
    subtitle: str | None
    build_id: int
    build_title: str
    record_class: str
    version_scope: str
    score: float | None = None
    tags: tuple[str, ...] = ()
    metrics: dict[str, str | int | float | bool] = field(default_factory=dict)
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
    resource_kind: Literal["metadata"] = field(default="metadata", init=False)


SearchHit: TypeAlias = RecordSearchHit | BuildSearchHit | MetadataSearchHit


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One offset-addressed page of search results.

    Relevance has no identifier sequence to anchor to, so both neighbours are always addressed by
    offset; the shared anchor type is used so that every page in the system reads the same way.
    """

    hits: tuple[SearchHit, ...]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None
    warnings: tuple[str, ...] = ()
    generated_at: datetime | None = None
