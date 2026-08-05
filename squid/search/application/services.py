"""Transport-neutral search orchestration."""

from typing import Protocol

from squid.core.errors import ValidationError
from squid.search.application.cursor import CursorCodec
from squid.search.application.fields import FieldRegistry
from squid.search.application.parser import SearchQueryParser
from squid.search.application.ports import SearchBackend
from squid.search.domain import CursorPosition, SearchPage, SearchQuery, SearchRequest


class SearchFieldRegistryProvider(Protocol):
    """Load the current public search-field catalog."""

    async def registry(self) -> FieldRegistry: ...


class SearchService:
    """Parse, validate, paginate, and delegate search execution."""

    def __init__(
        self,
        backend: SearchBackend,
        parser: SearchQueryParser,
        cursors: CursorCodec,
        fields: SearchFieldRegistryProvider | None = None,
    ) -> None:
        self._backend = backend
        self._parser = parser
        self._cursors = cursors
        self._fields = fields

    async def search(self, request: SearchRequest) -> SearchPage:
        """Execute a search and encode its next cursor."""
        query = await self._parse(request.query)
        cursor = self._decode_cursor(request)
        result = await self._backend.search(request, query, cursor)
        next_cursor = None
        if result.has_more and result.last_position is not None:
            next_cursor = self._cursors.encode(result.last_position)
        return SearchPage(
            hits=result.hits,
            next_cursor=next_cursor,
            has_more=result.has_more,
            warnings=result.warnings,
        )

    async def suggest(self, query: str | SearchQuery, *, limit: int = 5) -> tuple[str, ...]:
        """Suggest indexed terms for a valid query."""
        if not 1 <= limit <= 25:
            msg = "suggestion limit must be between 1 and 25"
            raise ValidationError(msg, public_context={"field": "limit", "minimum": 1, "maximum": 25})
        parsed = await self._parse(query) if isinstance(query, str) else query
        return await self._backend.suggest(parsed, limit=limit)

    async def _parse(self, query: str) -> SearchQuery:
        if self._fields is None:
            return self._parser.parse(query)
        return SearchQueryParser(await self._fields.registry()).parse(query)

    def _decode_cursor(self, request: SearchRequest) -> CursorPosition | None:
        if request.cursor is None:
            return None
        return self._cursors.decode(request.cursor, request=request)
