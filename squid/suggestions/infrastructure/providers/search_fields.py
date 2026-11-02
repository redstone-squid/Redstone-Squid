"""Suggestion providers over the public search grammar.

The field registry is already the published contract for what may be queried
(`GET /v1/search/fields`); these turn it into completions so the vocabulary does not have to be
memorized from documentation.
"""

from typing import Protocol

from squid.search.application.fields import FieldRegistry
from squid.suggestions.application import Candidate, candidate
from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.cache import TtlCache


class SearchFields(Protocol):
    """Read the effective public field registry."""

    async def fields(self) -> FieldRegistry: ...


class SearchFieldProvider:
    """Suggest queryable field names."""

    def __init__(self, search: SearchFields) -> None:
        self._search = search
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        registry = await self._search.fields()
        return tuple(
            candidate(
                value=field.name,
                label=field.name,
                description=_field_hint(field.value_type.value, field.supports_range),
                kind="field",
                terms=(field.name, *field.aliases),
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
        self._cache = TtlCache[None, tuple[Candidate, ...]](self._load)

    async def candidates(self, request: SuggestionRequest) -> tuple[Candidate, ...]:
        del request
        return await self._cache.get(None)

    async def _load(self, _key: None) -> tuple[Candidate, ...]:
        registry = await self._search.fields()
        sortable = sorted(
            (field for field in registry.definitions if field.supports_sort),
            key=lambda item: item.name,
        )
        results: list[Candidate] = []
        for field in sortable:
            results.append(candidate(field.name, f"{field.name} (ascending)", kind="sort"))
            results.append(candidate(f"-{field.name}", f"{field.name} (descending)", kind="sort"))
        return tuple(results)


def _field_hint(value_type: str, supports_range: bool) -> str:
    return f"{value_type}, ranges supported" if supports_range else value_type
