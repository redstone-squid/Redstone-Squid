"""Search application extension points implemented by persistence adapters."""

from dataclasses import dataclass
from typing import Protocol

from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.search.domain import SearchHit, SearchQuery, SearchRequest


@dataclass(frozen=True, slots=True)
class SearchSlice:
    """A page of backend results and the total available for pagination.

    Ranked searches may limit the candidate pool, so total need not count every
    document that matches the filters.
    """

    hits: tuple[SearchHit, ...]
    total: int
    warnings: tuple[str, ...] = ()


class SearchBackend(Protocol):
    """Persistence operations required by the search application service."""

    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        *,
        offset: int,
        registry: FieldRegistry = DEFAULT_FIELD_REGISTRY,
        query_embedding: QueryEmbedding | None = None,
    ) -> SearchSlice: ...

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class QueryEmbedding:
    """A query vector and the model used to generate it."""

    vector: list[float]
    model: str
