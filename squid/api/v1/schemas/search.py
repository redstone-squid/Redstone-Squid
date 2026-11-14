"""Public search metadata and result representations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from squid.builds.application.queries import PublicBuildSummary
from squid.core.errors import ValidationError
from squid.search.domain import MetadataSearchHit, RecordSearchHit, SearchMatch, SearchText


class SearchSuggestions(BaseModel):
    """Indexed terms completing a partial query."""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[str]


class RecordSearchEntry(BaseModel):
    """Record facts copied from the search index.

    These values are returned without reloading the record or its holder build.
    They reflect the last projection update and may lag behind current data.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: int
    title: str
    subtitle: str | None
    build_id: int
    build_title: str
    base_title: str
    version_scope: str
    tags: list[str]
    metrics: dict[str, str | int | float | bool]
    holder_build_ids: list[int] = Field(default_factory=list)
    matched: bool = True
    title_match: SearchText | None = None

    @classmethod
    def from_domain(cls, hit: RecordSearchHit) -> RecordSearchEntry:
        return cls(
            record_id=_record_id(hit.source_id),
            title=hit.title,
            subtitle=hit.subtitle,
            build_id=hit.build_id,
            build_title=hit.build_title,
            base_title=hit.base_title,
            version_scope=hit.version_scope,
            tags=list(hit.tags),
            metrics=dict(hit.metrics),
            holder_build_ids=list(hit.holder_build_ids),
            matched=hit.matched,
            title_match=hit.title_match,
        )


class MetadataSearchEntry(BaseModel):
    """A matched taxonomy or version entry."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    metadata_kind: str
    description: str | None
    aliases: list[str]

    @classmethod
    def from_domain(cls, hit: MetadataSearchHit) -> MetadataSearchEntry:
        return cls(
            id=hit.source_id,
            title=hit.title,
            metadata_kind=hit.metadata_kind,
            description=hit.description,
            aliases=list(hit.aliases),
        )


class BuildSearchResult(BaseModel):
    """A build match, hydrated from the authoritative record."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["build"] = "build"
    score: float | None
    build: PublicBuildSummary
    grouped: bool = False
    records: list[RecordSearchEntry] = Field(default_factory=list)
    matches: list[SearchMatch] = Field(default_factory=list)
    title_match: SearchText | None = None


class RecordSearchResult(BaseModel):
    """A computed record match."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["record"] = "record"
    score: float | None
    record: RecordSearchEntry


class MetadataSearchResult(BaseModel):
    """A taxonomy or version match."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["metadata"] = "metadata"
    score: float | None
    metadata: MetadataSearchEntry
    matches: list[SearchMatch] = Field(default_factory=list)
    title_match: SearchText | None = None


type SearchResult = Annotated[
    BuildSearchResult | RecordSearchResult | MetadataSearchResult,
    Field(discriminator="resource_kind"),
]


def _record_id(source_id: str) -> int:
    """Parse the ``result:<id>`` projection key records are indexed under."""
    _, separator, raw_id = source_id.partition(":")
    try:
        return int(raw_id if separator else source_id)
    except ValueError as error:
        msg = "search returned an invalid record identifier"
        raise ValidationError(msg) from error
