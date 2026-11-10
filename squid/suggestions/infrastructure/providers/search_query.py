"""Complete field names and fixed or indexed values within a search query.

Results include the character range to replace, preserving the rest of the query.
"""

from collections.abc import Sequence
from typing import Protocol

from squid.search.application.completion import (
    BOOLEAN_KEYWORDS,
    CompletionContext,
    CompletionKind,
    analyze,
    completes_values,
    render_value,
)
from squid.search.application.fields import FieldRegistry
from squid.suggestions.application import Candidate, rank
from squid.suggestions.domain import (
    MAX_SUGGESTIONS,
    ReplacementSpan,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
)


class SearchFields(Protocol):
    """Read the effective public field registry."""

    async def fields(self) -> FieldRegistry: ...


class FacetReader(Protocol):
    """Read indexed values of one projected facet."""

    async def facet_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]: ...


class SearchQueryProvider:
    """Complete the token under the caret in a search query.

    Composed rather than candidate-producing because the caret position changes both what may be
    suggested and what a chosen value replaces, neither of which a flat candidate list can carry.
    """

    def __init__(self, search: SearchFields, facets: FacetReader) -> None:
        self._search = search
        self._facets = facets

    async def suggest(self, request: SuggestionRequest) -> SuggestionResult:
        registry = await self._search.fields()
        context = analyze(request.query, request.cursor, registry)
        limit = request.limit or MAX_SUGGESTIONS
        span = ReplacementSpan(context.start, context.end)

        match context.kind:
            case CompletionKind.RANGE:
                # Range bounds are free-form; this provider does not suggest indexed values for them.
                return SuggestionResult(replacement=span)
            case CompletionKind.FIELD_VALUE:
                return SuggestionResult(items=await self._values(context, limit), replacement=span)
            case CompletionKind.TERM:
                return SuggestionResult(items=_terms(context, registry, limit), replacement=span)

    async def _values(self, context: CompletionContext, limit: int) -> tuple[Suggestion, ...]:
        assert context.field is not None
        if not completes_values(context.field):
            return ()
        if context.field.suggested_values is not None:
            prefix = context.prefix.strip().casefold()
            values = sorted(value for value in context.field.suggested_values if value.casefold().startswith(prefix))[
                :limit
            ]
        else:
            values = await self._facets.facet_values(context.field.name, context.prefix, limit=limit)
        # Preserve prefix matching and ordering when quoting the values.
        return tuple(
            Suggestion(
                value=render_value(value, quoted=context.quoted),
                label=value,
                kind=context.field.name,
            )
            for value in values
        )


def _terms(context: CompletionContext, registry: FieldRegistry, limit: int) -> tuple[Suggestion, ...]:
    """Offer field names, and boolean keywords once there is something to combine."""
    candidates: list[Candidate] = [
        # The colon is part of the value: picking a field should leave the caret ready to type a
        # value rather than making the user punctuate it themselves.
        Candidate.create(
            f"{field.name}:",
            label=f"{field.name}:",
            description=field.value_type.value,
            kind="field",
            terms=(field.name, *field.aliases),
        )
        for field in sorted(registry.definitions, key=lambda item: item.name)
    ]
    if context.prefix:
        candidates.extend(
            Candidate.create(keyword, description="combine terms", kind="operator") for keyword in BOOLEAN_KEYWORDS
        )
    return rank(context.prefix, candidates, limit=limit)
