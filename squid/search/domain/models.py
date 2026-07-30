"""Transport-neutral search requests and results."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, TypeAlias


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


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """A normalized search request passed to an application service."""

    query: str
    scope: SearchScope = SearchScope.RECORDS
    mode: SearchMode = SearchMode.LEXICAL
    page_size: int = 5
    cursor: str | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.page_size <= 50:
            msg = "page_size must be between 1 and 50"
            raise ValueError(msg)


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
class CursorPosition:
    """Stable ordering position encoded into an opaque cursor."""

    query_hash: str
    scope: SearchScope
    mode: SearchMode
    score: float | None
    resource_kind: Literal["record", "build", "metadata"]
    source_id: str


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One cursor-addressable page of search results."""

    hits: tuple[SearchHit, ...]
    next_cursor: str | None
    has_more: bool
    warnings: tuple[str, ...] = ()
    generated_at: datetime | None = None
