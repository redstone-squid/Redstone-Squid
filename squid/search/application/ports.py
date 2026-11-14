"""Search application extension points implemented by persistence adapters."""

from dataclasses import dataclass
from typing import Protocol

from squid.search.domain import FieldDefinition, SearchHit, SearchQuery, SearchRequest


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
        sort_field: FieldDefinition | None = None,
        query_embedding: QueryEmbedding | None = None,
    ) -> SearchSlice:
        """Return the page of matches that starts at `request.offset`.

        `query` and `sort_field` are `request.query` and `request.sort` after the
        service has parsed and resolved them against the field registry.
        """
        ...

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class QueryEmbedding:
    """A query vector and the model used to generate it."""

    vector: list[float]
    model: str
