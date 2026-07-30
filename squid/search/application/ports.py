"""Search application extension points implemented by persistence adapters."""

from dataclasses import dataclass
from typing import Protocol

from squid.search.domain import CursorPosition, SearchHit, SearchQuery, SearchRequest


class SearchQueryCompiler[CompiledQueryT](Protocol):
    """Compile a typed query AST into an adapter-specific, parameterized query."""

    def compile(self, query: SearchQuery) -> CompiledQueryT:
        """Compile without interpreting user text as identifiers or code."""
        ...


@dataclass(frozen=True, slots=True)
class SearchSlice:
    """Backend results plus the last returned ordering position."""

    hits: tuple[SearchHit, ...]
    has_more: bool
    last_position: CursorPosition | None
    warnings: tuple[str, ...] = ()


class SearchBackend(Protocol):
    """Persistence operations required by the search application service."""

    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        cursor: CursorPosition | None,
    ) -> SearchSlice: ...

    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]: ...
