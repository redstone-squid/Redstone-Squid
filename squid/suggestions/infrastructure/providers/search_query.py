"""Cursor-aware completion inside the search query language.

This is the source that makes the grammar usable without reading its documentation: it completes
field names where a field name may go, indexed values where that field's values may go, and
returns the span each replaces so a client splices at the caret instead of replacing the box.
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
from squid.suggestions.application import Candidate, candidate, rank
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
                # Inside `[low TO high]` the only thing left to type is a bound, and the indexed
                # values of a numeric field are not a list anybody wants to scroll.
                return SuggestionResult(replacement=span)
            case CompletionKind.FIELD_VALUE:
                return SuggestionResult(items=await self._values(context, limit), replacement=span)
            case CompletionKind.TERM:
                return SuggestionResult(items=_terms(context, registry, limit), replacement=span)

    async def _values(self, context: CompletionContext, limit: int) -> tuple[Suggestion, ...]:
        assert context.field is not None
        if not completes_values(context.field):
            return ()
        values = await self._facets.facet_values(context.field.name, context.prefix, limit=limit)
        # Postgres already prefix-matched and ordered these, so they are returned as-is rather
        # than run back through the matcher, which would only discard what it cannot re-derive.
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
        candidate(
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
            candidate(keyword, description="combine terms", kind="operator") for keyword in BOOLEAN_KEYWORDS
        )
    return rank(context.prefix, candidates, limit=limit)
