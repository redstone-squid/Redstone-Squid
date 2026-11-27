"""Search results. Record hits carry holder summaries from the records application."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from squid.core.pagination import PageAnchor
from squid.core.records.application.metrics import RecordHolderSummary, RecordMetric
from squid.core.search.domain import QueryClause, SearchEvidence, SearchMatch


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
    metrics: Mapping[str, RecordMetric] = field(default_factory=dict)
    """The measurements of `build_id`'s holder."""
    holder_build_ids: tuple[int, ...] = ()
    holders: tuple[RecordHolderSummary, ...] = ()
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
    clauses: tuple[QueryClause, ...] = ()
    """The clauses of `SearchRequest.query`; empty when the request supplied a parsed query."""
