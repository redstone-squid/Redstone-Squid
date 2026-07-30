"""Search application extension points implemented by persistence adapters."""

from typing import Protocol

from squid.search.domain import SearchQuery


class SearchQueryCompiler[CompiledQueryT](Protocol):
    """Compile a typed query AST into an adapter-specific, parameterized query."""

    def compile(self, query: SearchQuery) -> CompiledQueryT:
        """Compile without interpreting user text as identifiers or code."""
        ...
