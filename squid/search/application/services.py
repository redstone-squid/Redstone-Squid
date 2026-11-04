"""Search orchestration shared by the bot and API."""

import logging
from typing import Protocol

from squid.core.errors import ValidationError
from squid.core.pagination import PageAnchor
from squid.search.application.embeddings import SearchEmbeddingModel
from squid.search.application.fields import FieldRegistry
from squid.search.application.parser import SearchQueryParser
from squid.search.application.ports import QueryEmbedding, SearchBackend
from squid.search.application.query_analysis import is_filter_only
from squid.search.domain import SearchMode, SearchPage, SearchQuery, SearchRequest
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


class SearchFieldRegistryProvider(Protocol):
    """Load the current public search-field catalog."""

    async def registry(self) -> FieldRegistry: ...


class SearchService:
    """Parse and page searches, generating query embeddings outside database scopes."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[SearchUnitOfWork],
        embedding_model: SearchEmbeddingModel | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._embedding_model = embedding_model

    async def search(self, request: SearchRequest) -> SearchPage:
        registry = await self.fields()
        query = SearchQueryParser(registry).parse(request.query)
        query_embedding = None
        if (
            request.mode is SearchMode.SEMANTIC
            and request.sort is None
            and not is_filter_only(query)
            and self._embedding_model is not None
        ):
            try:
                vector = await self._embedding_model.embed(request.query)
                if vector is not None:
                    query_embedding = QueryEmbedding(vector, self._embedding_model.model_name)
            except Exception:
                logger.exception("Search query embedding generation failed")
        async with self._uow_factory() as uow:
            result = await uow.search.search(
                request, query, offset=request.offset, registry=registry, query_embedding=query_embedding
            )
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
        async with self._uow_factory() as uow:
            parsed = (
                SearchQueryParser(await uow.search_fields.registry()).parse(query) if isinstance(query, str) else query
            )
            return await uow.search.suggest(parsed, limit=limit)

    async def fields(self) -> FieldRegistry:
        """Return the field registry used by query parsing."""
        async with self._uow_factory() as uow:
            return await uow.search_fields.registry()


class SearchUnitOfWork(UnitOfWork, Protocol):
    @property
    def search(self) -> SearchBackend: ...

    @property
    def search_fields(self) -> SearchFieldRegistryProvider: ...
