"""Transport-neutral search orchestration."""

from typing import Protocol

from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.search.application.parser import SearchQueryParser
from squid.search.application.ports import SearchBackend
from squid.search.domain import SearchPage, SearchQuery, SearchRequest


class SearchFieldRegistryProvider(Protocol):
    """Load the current public search-field catalog."""

    async def registry(self) -> FieldRegistry: ...


class SearchService:
    """Parse, validate, paginate, and delegate search execution."""

    def __init__(
        self,
        backend: SearchBackend,
        parser: SearchQueryParser,
        fields: SearchFieldRegistryProvider | None = None,
    ) -> None:
        self._backend = backend
        self._parser = parser
        self._fields = fields

    async def search(self, request: SearchRequest) -> SearchPage:
        """Execute a search and address the pages adjacent to it."""
        query = await self._parse(request.query)
        result = await self._backend.search(request, query, offset=request.offset)
        offset = request.offset
        following = offset + request.page_size
        return SearchPage(
            hits=result.hits,
            total=result.total,
            next=PageAnchor(offset=following) if following < result.total else None,
            prev=PageAnchor(offset=max(offset - request.page_size, 0)) if offset else None,
            warnings=result.warnings,
        )

    async def suggest(self, query: str | SearchQuery, *, limit: int = 5) -> tuple[str, ...]:
        """Suggest indexed terms for a valid query."""
        if not 1 <= limit <= 25:
            msg = "suggestion limit must be between 1 and 25"
            raise ValidationError(msg, public_context={"field": "limit", "minimum": 1, "maximum": 25})
        parsed = await self._parse(query) if isinstance(query, str) else query
        return await self._backend.suggest(parsed, limit=limit)

    async def fields(self) -> FieldRegistry:
        """Return the effective public field registry used by query parsing."""
        if self._fields is None:
            return DEFAULT_FIELD_REGISTRY
        return await self._fields.registry()

    async def _parse(self, query: str) -> SearchQuery:
        if self._fields is None:
            return self._parser.parse(query)
        return SearchQueryParser(await self._fields.registry()).parse(query)
