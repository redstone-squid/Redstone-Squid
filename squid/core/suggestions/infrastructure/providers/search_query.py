"""Complete field names and fixed or indexed values within a search query.

Results include the character range to replace, preserving the rest of the query.
"""

from collections.abc import Sequence
from typing import Protocol

from squid.core.search.application.completion import (
    BOOLEAN_KEYWORDS,
    CompletionContext,
    CompletionKind,
    analyze,
    completes_values,
    render_value,
)
from squid.core.search.application.fields import FieldRegistry
from squid.core.search.domain.scope import SearchScope
from squid.core.suggestions.application import Candidate, rank
from squid.core.suggestions.application.ports import SearchFields
from squid.core.suggestions.application.queries import SuggestionRepository
from squid.core.suggestions.application.registry import SuggestionSource
from squid.core.suggestions.domain import (
    MAX_QUERY_SUGGESTIONS,
    ReplacementSpan,
    SourceKind,
    Suggestion,
    SuggestionRequest,
    SuggestionResult,
)
from squid.core.suggestions.infrastructure.cache import TtlCache
from squid.core.suggestions.infrastructure.providers.search_fields import (
    SearchFieldProvider,
    SearchSortProvider,
    suggestion_search_scope,
)
from squid.core.suggestions.infrastructure.providers.tags import TagReader
from squid.core.tags.domain import TagSemanticKind


class FilterValueReader(TagReader, Protocol):
    """Read indexed values and creator profiles for search completions."""

    async def filter_values(self, field_name: str, prefix: str, *, limit: int) -> Sequence[str]: ...

    async def creator_profiles(self, query: str, *, limit: int) -> Sequence[tuple[str, str]]: ...


class SearchQueryProvider:
    """Complete the token under the caret in a search query.

    Composed rather than candidate-producing because the caret position changes both what may be
    suggested and what a chosen value replaces, neither of which a flat candidate list can carry.
    """

    def __init__(self, search: SearchFields, filters: FilterValueReader) -> None:
        self._search = search
        self._filters = filters
        self._tags = TtlCache(self._load_tags)

    async def _load_tags(self, _key: None) -> tuple[Candidate, ...]:
        candidates = []
        for kind in TagSemanticKind:
            field = "tag" if kind is TagSemanticKind.SHOWCASE else kind.value
            entries = await self._filters.tag_entries(kind, authority=None)
            candidates.extend(
                Candidate.create(
                    f"{field}:{render_value(entry.display_name, quoted=True)}",
                    label=entry.display_name,
                    kind=field,
                    terms=(entry.display_name, entry.stable_key, *entry.aliases),
                    priority=-1 if entry.authority == "user" else 0,
                )
                for entry in entries
            )
        return tuple(candidates)

    async def suggest(self, request: SuggestionRequest) -> SuggestionResult:
        scope = suggestion_search_scope(request)
        registry = (await self._search.fields()).for_scope(scope)
        context = analyze(request.query, request.cursor, registry)
        limit = request.limit or MAX_QUERY_SUGGESTIONS
        span = ReplacementSpan(context.start, context.end)

        match context.kind:
            case CompletionKind.RANGE:
                # Range bounds are free-form; this provider does not suggest indexed values for them.
                return SuggestionResult(replacement=span)
            case CompletionKind.FIELD_VALUE:
                return SuggestionResult(items=await self._values(context, limit, scope), replacement=span)
            case CompletionKind.TERM:
                tags = await self._tags.get(None) if context.prefix and scope != SearchScope.METADATA else ()
                return SuggestionResult(items=_terms(context, registry, limit, tags), replacement=span)

    async def _values(self, context: CompletionContext, limit: int, scope: SearchScope) -> tuple[Suggestion, ...]:
        assert context.field is not None
        if not completes_values(context.field):
            return ()
        choices = context.field.choices(scope)
        if choices is not None:
            prefix = context.prefix.strip().casefold()
            values = sorted(value for value in choices if value.casefold().startswith(prefix))[:limit]
        else:
            values = await self._filters.filter_values(context.field.name, context.prefix, limit=limit)
        # Preserve prefix matching and ordering when quoting the values.
        names = tuple(
            Suggestion(
                value=render_value(value, quoted=context.quoted),
                label=value,
                kind=context.field.name,
            )
            for value in values
        )
        if context.field.name != "creator":
            return names
        # A creator profile matches every name its account is credited under, so it comes first.
        profiles = tuple(
            Suggestion(
                value=render_value(public_id, quoted=context.quoted),
                label=name,
                description="all builds by this creator",
                kind="creator_profile",
            )
            for public_id, name in await self._filters.creator_profiles(context.prefix, limit=limit)
        )
        return (profiles + names)[:limit]


def _terms(
    context: CompletionContext, registry: FieldRegistry, limit: int, tags: Sequence[Candidate]
) -> tuple[Suggestion, ...]:
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
    candidates.extend(tags)
    return rank(context.prefix, candidates, limit=limit)


def search_sources(
    search: SearchFields,
    repository: SuggestionRepository,
) -> tuple[SuggestionSource, ...]:
    return (
        SuggestionSource(
            id="search_query",
            provider=SearchQueryProvider(search, repository),
            kind_label="query",
        ),
        SuggestionSource(
            id="search_fields",
            provider=SearchFieldProvider(search),
            kind=SourceKind.ENUMERABLE,
            kind_label="field",
        ),
        SuggestionSource(
            id="search_sorts",
            provider=SearchSortProvider(search),
            kind=SourceKind.ENUMERABLE,
            kind_label="sort",
        ),
    )
