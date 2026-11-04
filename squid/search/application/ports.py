"""Search application extension points implemented by persistence adapters."""

from dataclasses import dataclass
from typing import Protocol

from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.search.domain import SearchHit, SearchQuery, SearchRequest


class SearchQueryCompiler[CompiledQueryT](Protocol):
    """Compile a typed query AST into an adapter-specific, parameterized query."""

    def compile(self, query: SearchQuery) -> CompiledQueryT:
        """Compile without interpreting user text as identifiers or code."""
        ...


@dataclass(frozen=True, slots=True)
class SearchSlice:
    """One offset-addressed window of backend results and the size of the full result set."""

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
