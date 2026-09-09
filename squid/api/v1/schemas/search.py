"""Public search metadata and result representations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from squid.api.v1.schemas.builds import BuildSummary
from squid.core.errors import ValidationError
from squid.search.application.fields import FieldDefinition
from squid.search.domain import MetadataSearchHit, RecordSearchHit


class SearchField(BaseModel):
    """One query field supported by the public search grammar."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Canonical name to use in a query.")
    type: str = Field(description="Value type accepted: `text`, `number`, `timestamp` or `boolean`.")
    supports_range: bool = Field(description="Whether the field accepts range comparisons rather than equality only.")
    supports_sort: bool = Field(description="Whether results may be ordered by this field.")
    aliases: list[str] = Field(description="Other names accepted for `name`, matched case-insensitively.")

    @classmethod
    def from_domain(cls, field: FieldDefinition) -> SearchField:
        return cls(
            name=field.name,
            type=field.value_type.value,
            supports_range=field.supports_range,
            supports_sort=field.supports_sort,
            aliases=list(field.aliases),
        )


class SearchSuggestions(BaseModel):
    """Indexed terms completing a partial query."""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[str] = Field(
        description="Indexed terms close to the query's positive text, at most the requested limit."
    )


class RecordSearchEntry(BaseModel):
    """Projection facts for a matched computed record.

    Every field here is derived by record computation rather than submitted by a user, so unlike
    a build projection it carries no free text and can be served without hydration.
    """

    model_config = ConfigDict(extra="forbid")

    record_id: int
    title: str
    subtitle: str | None
    build_id: int = Field(description="The top-ranked holder build this entry is titled after.")
    build_title: str
    record_class: str = Field(
        description="Normally one of `first`, `fastest`, `smallest`, `fastest_smallest` or `smallest_fastest`."
    )
    version_scope: str = Field(description="Normally `all_time` or `current`.")
    tags: list[str]
    metrics: dict[str, str | int | float | bool] = Field(
        description="Indexed measurements of the holder build, keyed by metric name. Which keys appear depends on the "
        "record, so read them by name."
    )

    @classmethod
    def from_domain(cls, hit: RecordSearchHit) -> RecordSearchEntry:
        return cls(
            record_id=_record_id(hit.source_id),
            title=hit.title,
            subtitle=hit.subtitle,
            build_id=hit.build_id,
            build_title=hit.build_title,
            record_class=hit.record_class,
            version_scope=hit.version_scope,
            tags=list(hit.tags),
            metrics=dict(hit.metrics),
        )


class MetadataSearchEntry(BaseModel):
    """A matched taxonomy or version entry."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Opaque projection key. Match it for equality; do not parse it.")
    title: str
    metadata_kind: str = Field(
        description="What was matched: `creator`, `version`, or a tag's semantic kind (`restriction`, `pattern` or "
        "`showcase`)."
    )
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
    score: float | None = Field(
        description="Relevance, higher first. Comparable within one response only, never across queries; null when "
        "the results carry no ranking."
    )
    build: BuildSummary


class RecordSearchResult(BaseModel):
    """A computed record match."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["record"] = "record"
    score: float | None = Field(
        description="Relevance, higher first. Comparable within one response only, never across queries; null when "
        "the results carry no ranking."
    )
    record: RecordSearchEntry


class MetadataSearchResult(BaseModel):
    """A taxonomy or version match."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["metadata"] = "metadata"
    score: float | None = Field(
        description="Relevance, higher first. Comparable within one response only, never across queries; null when "
        "the results carry no ranking."
    )
    metadata: MetadataSearchEntry


type SearchResult = Annotated[
    BuildSearchResult | RecordSearchResult | MetadataSearchResult,
    Field(discriminator="resource_kind"),
]


def _record_id(source_id: str) -> int:
    """Parse the `result:<id>` projection key records are indexed under, or a bare id.

    Raises `ValidationError` when the id part is not an integer.
    """
    _, separator, raw_id = source_id.partition(":")
    try:
        return int(raw_id if separator else source_id)
    except ValueError as error:
        msg = "search returned an invalid record identifier"
        raise ValidationError(msg) from error
