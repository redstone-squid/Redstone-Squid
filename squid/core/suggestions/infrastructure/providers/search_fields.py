"""Suggestion providers over the public search grammar.

The field registry is already the published contract for what may be queried
(`GET /v1/search/fields`); these turn it into completions so the vocabulary does not have to be
memorized from documentation.
"""

from squid.core.actors import ActingContext
from squid.core.i18n import localization_for, negotiate_locale
from squid.core.search.domain.scope import SearchScope
from squid.core.suggestions.application import Candidate
from squid.core.suggestions.application.ports import SearchFields
from squid.core.suggestions.domain import SuggestionRequest
from squid.core.suggestions.infrastructure.cache import TtlCache
from squid_i18n import localization_scope


class SearchFieldProvider:
    """Suggest queryable field names, labelled in the request's language."""

    def __init__(self, search: SearchFields) -> None:
        self._search = search
        self._cache = TtlCache[tuple[SearchScope, str], tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        return await self._cache.get((suggestion_search_scope(request), negotiate_locale(request.locale)))

    async def _load(self, key: tuple[SearchScope, str]) -> tuple[Candidate, ...]:
        scope, locale = key
        registry = (await self._search.fields(context=ActingContext.current())).for_scope(scope)
        with localization_scope(localization_for(locale)):
            return tuple(
                Candidate.create(
                    value=field.name,
                    label=(label := field.display_name.resolve()),
                    description=field.name,
                    kind="field",
                    terms=(label, field.name, *field.aliases),
                )
                for field in sorted(registry.definitions, key=lambda item: item.name)
            )


class SearchSortProvider:
    """Suggest the fields a search may be sorted on, in both directions.

    Sort values are emitted in the `field` / `-field` form the API already parses, so picking one
    is a complete answer rather than a fragment the user still has to punctuate.
    """

    def __init__(self, search: SearchFields) -> None:
        self._search = search
        self._cache = TtlCache[str, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        return await self._cache.get(negotiate_locale(request.locale))

    async def _load(self, locale: str) -> tuple[Candidate, ...]:
        registry = await self._search.fields(context=ActingContext.current())
        sortable = sorted(
            (field for field in registry.definitions if field.supports_sort),
            key=lambda item: item.name,
        )
        with localization_scope(localization_for(locale)):
            return tuple(
                Candidate.create(value, registry.sort_label(value).resolve(), kind="sort")
                for field in sortable
                for value in (field.name, f"-{field.name}")
            )


def suggestion_search_scope(request: SuggestionRequest) -> SearchScope:
    scope = request.context.get("scope", SearchScope.ALL)
    return SearchScope(scope) if scope in SearchScope else SearchScope.ALL
