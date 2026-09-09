"""Public representations of typeahead suggestions."""

from pydantic import ConfigDict, Field

from squid.api.schema import ApiSchema
from squid.suggestions.application import SuggestionSource
from squid.suggestions.domain import (
    ReplacementSpan,
    SourceKind,
    Suggestion,
    SuggestionResult,
    ValueType,
)


class SuggestionItem(ApiSchema):
    """One candidate completion.

    `value` is what a client submits and `label` is what it shows. They differ wherever a command
    or form field stores an identifier the user should never have to see.
    """

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    description: str | None = Field(
        description="Secondary text disambiguating candidates that share a label; null when there is none."
    )
    kind: str = Field(description="The entity type behind the candidate, for keeping a mixed list readable.")

    @classmethod
    def from_domain(cls, suggestion: Suggestion) -> SuggestionItem:
        return cls(
            value=suggestion.value,
            label=suggestion.label,
            description=suggestion.description,
            kind=suggestion.kind,
        )


class SuggestionReplacement(ApiSchema):
    """The half-open range of the submitted query a value replaces.

    Splice the chosen `value` over it rather than clobbering the whole input.
    """

    model_config = ConfigDict(extra="forbid")

    start: int = Field(description="Character offset of the first replaced character.")
    end: int = Field(description="Character offset just past the last replaced character.")

    @classmethod
    def from_domain(cls, span: ReplacementSpan) -> SuggestionReplacement:
        return cls(start=span.start, end=span.end)


class SuggestionPage(ApiSchema):
    """Ranked completions for one partially typed value."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(description="The source these completions came from.")
    revision: int | None = Field(
        description="Content revision of an enumerable source, matching the response `ETag`. Null for a queried "
        "source, which has no enumerable content to revision."
    )
    replacement: SuggestionReplacement | None = Field(
        description="Present when a value completes part of the query; null when it replaces all of it."
    )
    items: list[SuggestionItem] = Field(description="Ranked, best first.")

    @classmethod
    def from_domain(cls, source: str, result: SuggestionResult) -> SuggestionPage:
        return cls(
            source=source,
            revision=result.revision,
            replacement=(None if result.replacement is None else SuggestionReplacement.from_domain(result.replacement)),
            items=[SuggestionItem.from_domain(item) for item in result.items],
        )


class SuggestionSourceInfo(ApiSchema):
    """What a client needs to know to drive one source without hardcoding it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: SourceKind = Field(
        description="`enumerable` sources can be listed and cached whole; `queried` ones must be asked per query."
    )
    value_type: ValueType = Field(description="Scalar type each `value` carries: `string` or `integer`.")
    context_keys: list[str] = Field(
        description="Context this source needs supplied, such as `category`, sorted ascending."
    )
    multi_value: str | None = Field(
        description="Separator when the completed parameter holds a list, so a client splices one entry. Null for a "
        "single-valued parameter."
    )
    requires_authentication: bool = Field(
        description="True when the source is gated or scoped, so an anonymous caller gets nothing."
    )

    @classmethod
    def from_domain(cls, source: SuggestionSource) -> SuggestionSourceInfo:
        return cls(
            id=source.id,
            kind=source.kind,
            value_type=source.value_type,
            context_keys=sorted(source.context_keys),
            multi_value=source.multi_value,
            requires_authentication=source.visibility.value != "public",
        )
