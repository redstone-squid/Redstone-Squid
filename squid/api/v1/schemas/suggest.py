"""Public representations of typeahead suggestions."""

from pydantic import BaseModel, ConfigDict

from squid.suggestions.application import SuggestionSource
from squid.suggestions.domain import (
    ReplacementSpan,
    SourceKind,
    Suggestion,
    SuggestionResult,
    ValueType,
)


class SuggestionItem(BaseModel):
    """One candidate completion.

    `value` is what a client submits and `label` is what it shows. They differ wherever a command
    or form field stores an identifier the user should never have to see.
    """

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str
    description: str | None
    kind: str

    @classmethod
    def from_domain(cls, suggestion: Suggestion) -> "SuggestionItem":
        return cls(
            value=suggestion.value,
            label=suggestion.label,
            description=suggestion.description,
            kind=suggestion.kind,
        )


class SuggestionReplacement(BaseModel):
    """The half-open range of the submitted query a value replaces."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int

    @classmethod
    def from_domain(cls, span: ReplacementSpan) -> "SuggestionReplacement":
        return cls(start=span.start, end=span.end)


class SuggestionPage(BaseModel):
    """Ranked completions for one partially typed value."""

    model_config = ConfigDict(extra="forbid")

    source: str
    revision: int | None
    """Content revision of an enumerable source, matching the response `ETag`."""

    replacement: SuggestionReplacement | None
    """Present when the value completes part of the query rather than all of it."""

    items: list[SuggestionItem]

    @classmethod
    def from_domain(cls, source: str, result: SuggestionResult) -> "SuggestionPage":
        return cls(
            source=source,
            revision=result.revision,
            replacement=(None if result.replacement is None else SuggestionReplacement.from_domain(result.replacement)),
            items=[SuggestionItem.from_domain(item) for item in result.items],
        )


class SuggestionSourceInfo(BaseModel):
    """What a client needs to know to drive one source without hardcoding it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: SourceKind
    value_type: ValueType
    context_keys: list[str]
    multi_value: str | None
    requires_authentication: bool
    """True when the source is gated or scoped, so an anonymous caller gets nothing."""

    @classmethod
    def from_domain(cls, source: SuggestionSource) -> "SuggestionSourceInfo":
        return cls(
            id=source.id,
            kind=source.kind,
            value_type=source.value_type,
            context_keys=sorted(source.context_keys),
            multi_value=source.multi_value,
            requires_authentication=source.visibility.value != "public",
        )
