"""Public search metadata and result representations."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from squid.api.pagination import PageAnchor
from squid.core.builds.application.queries import PublicBuildSummary
from squid.core.errors import ValidationError
from squid.core.search.domain import (
    ComparisonOperator,
    FieldDefinition,
    FieldExpression,
    FieldGroup,
    FieldMatch,
    FieldType,
    MetadataSearchHit,
    NotExpression,
    PresenceExpression,
    QueryClause,
    RangeValue,
    RecordSearchHit,
    SearchMatch,
    SearchScope,
    TextExpression,
)
from squid.core.search.domain.models import RecordSearchHolder
from squid.core.search.domain.query import ScalarValue


class SearchFieldChoice(BaseModel):
    """An accepted value of an enumerated field."""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    scopes: list[SearchScope] = Field(description="Catalogue tabs that accept this value.")


class SearchField(BaseModel):
    """A field queries can filter or sort on, labelled in the response language."""

    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str]
    type: FieldType
    label: str
    description: str | None
    group: FieldGroup
    unit: str | None = Field(description="The unit of a number written without a suffix, such as `gt`.")
    scopes: list[SearchScope] = Field(
        description="Catalogue tabs that can use the field. The all and metadata scopes accept every field."
    )
    supports_range: bool
    supports_sort: bool
    metric: bool = Field(description="A holder measurement that the records table shows as a column.")
    choices: list[SearchFieldChoice] | None = Field(description="Every accepted value, or null for free-form fields.")
    examples: list[str] = Field(description="Complete queries that use the field.")

    @classmethod
    def from_domain(cls, field: FieldDefinition) -> SearchField:
        """Resolve labels in the current localization scope."""
        choices = field.value_choices()
        return cls(
            name=field.name,
            aliases=list(field.aliases),
            type=field.value_type,
            label=field.display_name.resolve(),
            description=field.description.resolve() if field.description is not None else None,
            group=field.group,
            unit=field.unit,
            scopes=sorted(field.scopes),
            supports_range=field.supports_range,
            supports_sort=field.supports_sort,
            metric=field.holder_scoped,
            choices=None
            if choices is None
            else [
                SearchFieldChoice(value=choice.value, label=choice.label.resolve(), scopes=sorted(choice.scopes))
                for choice in choices
            ],
            examples=list(field.examples),
        )


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

    record_id: UUID
    title: str
    subtitle: str | None
    build_id: int
    build_title: str
    criterion: str
    version_scope: str
    metrics: dict[str, str | int | float | bool]
    holder_build_ids: list[int] = Field(default_factory=list)
    holders: list[RecordSearchHolder] = Field(default_factory=list)
    matched: bool = True
    retired: bool = False
    evidence: list[FieldMatch] = Field(default_factory=list)
    """Values that made the record match, with character ranges to highlight."""

    @classmethod
    def from_domain(cls, hit: RecordSearchHit) -> RecordSearchEntry:
        return cls(
            record_id=_record_id(hit.source_id),
            title=hit.title,
            subtitle=hit.subtitle,
            build_id=hit.build_id,
            build_title=hit.build_title,
            criterion=hit.criterion,
            version_scope=hit.version_scope,
            metrics=dict(hit.metrics),
            holder_build_ids=list(hit.holder_build_ids),
            holders=list(hit.holders),
            matched=hit.matched,
            retired=hit.retired,
            evidence=list(hit.evidence.matches),
        )


class MetadataSearchEntry(BaseModel):
    """A matched tag or version entry."""

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
    evidence: list[FieldMatch] = Field(default_factory=list)
    """Values of this document that matched, with character ranges to highlight."""


class RecordSearchResult(BaseModel):
    """A computed record match."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["record"] = "record"
    score: float | None
    record: RecordSearchEntry


class MetadataSearchResult(BaseModel):
    """A tag or version match."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Literal["metadata"] = "metadata"
    score: float | None
    metadata: MetadataSearchEntry
    matches: list[SearchMatch] = Field(default_factory=list)
    evidence: list[FieldMatch] = Field(default_factory=list)
    """Values of this document that matched, with character ranges to highlight."""


type SearchResult = Annotated[
    BuildSearchResult | RecordSearchResult | MetadataSearchResult,
    Field(discriminator="resource_kind"),
]

type SearchFilterValue = str | int | float | bool


class SearchFilterRange(BaseModel):
    """The inclusive bounds of a range clause such as `width:[2 TO 4]`."""

    model_config = ConfigDict(extra="forbid")

    lower: SearchFilterValue
    upper: SearchFilterValue


class SearchFilter(BaseModel):
    """One clause of the parsed query, shown as a removable filter."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "field", "presence"]
    field: str | None = Field(description="The canonical field name, or null for bare text.")
    operator: ComparisonOperator | None
    value: SearchFilterValue | SearchFilterRange | None = Field(
        description="The coerced value: numbers in the field's unit, timestamps as ISO 8601. Null for presence."
    )
    is_set: bool | None = Field(description="For presence clauses, whether the field must have a value.")
    negated: bool
    text: str = Field(description="The clause in query syntax.")
    without: str = Field(description="The normalized query with this clause removed.")

    @classmethod
    def from_domain(cls, clause: QueryClause) -> SearchFilter:
        expression = clause.expression
        negated = isinstance(expression, NotExpression)
        if isinstance(expression, NotExpression):
            expression = expression.operand
        common = {"negated": negated, "text": clause.text, "without": clause.without}
        match expression:
            case TextExpression(value=value):
                return cls(kind="text", field=None, operator=None, value=value, is_set=None, **common)
            case PresenceExpression(field=field, is_set=is_set):
                return cls(kind="presence", field=field.name, operator=None, value=None, is_set=is_set, **common)
            case FieldExpression(field=field, operator=operator, value=RangeValue(lower=lower, upper=upper)):
                bounds = SearchFilterRange(lower=_filter_value(lower), upper=_filter_value(upper))
                return cls(kind="field", field=field.name, operator=operator, value=bounds, is_set=None, **common)
            case FieldExpression(field=field, operator=operator, value=value):
                assert not isinstance(value, RangeValue)
                return cls(
                    kind="field", field=field.name, operator=operator, value=_filter_value(value), is_set=None, **common
                )
            case _:
                msg = "Query leaves are text, field or presence clauses."
                raise AssertionError(msg)


def _filter_value(value: ScalarValue) -> SearchFilterValue:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class SearchCounts(BaseModel):
    """Matches in each scope tab; null where the query cannot run in that scope."""

    model_config = ConfigDict(extra="forbid")

    all: int | None
    builds: int | None
    records: int | None
    metadata: int | None

    @classmethod
    def from_domain(cls, counts: Mapping[SearchScope, int | None]) -> SearchCounts:
        return cls(**{scope.value: counts.get(scope) for scope in SearchScope})


class SearchResultPage(BaseModel):
    """One page of ranked search results with the parsed query's filters."""

    model_config = ConfigDict(extra="forbid")

    items: list[SearchResult]
    total: int
    next: PageAnchor | None
    prev: PageAnchor | None
    filters: list[SearchFilter]
    counts: SearchCounts | None = Field(
        description="Present when requested with `counts=true`. Counts ignore grouping and ranking limits."
    )


def _record_id(source_id: str) -> UUID:
    """Parse the ``record:<public ID>`` key records are indexed under."""
    _, separator, raw_id = source_id.partition(":")
    try:
        return UUID(raw_id if separator else source_id)
    except ValueError as error:
        msg = "search returned an invalid record identifier"
        raise ValidationError(msg) from error
