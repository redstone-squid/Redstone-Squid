"""Search orchestration shared by the bot and API."""

import logging
from dataclasses import replace
from typing import Protocol

from squid.core.actors import ActingContext, application_operation
from squid.core.errors import ErrorCode, ValidationError
from squid.core.pagination import PageAnchor
from squid.core.records.application.metrics import RecordHolderSummary
from squid.core.records.application.ports import PublicBuildSummaryReader
from squid.core.search.application.embeddings import SearchEmbeddingModel
from squid.core.search.application.fields import FieldRegistry
from squid.core.search.application.parser import SearchQueryParser
from squid.core.search.application.ports import QueryEmbedding, SearchBackend
from squid.core.search.application.query_analysis import is_filter_only
from squid.core.search.application.results import BuildSearchHit, RecordSearchHit, SearchHit, SearchPage
from squid.core.search.domain import QueryClause, SearchMode, SearchQuery, SearchRequest, SearchScope
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory
from squid.core.validation import (
    AboveMaximum,
    AboveMaximumParams,
    BelowMinimum,
    BelowMinimumParams,
    FieldErrorCode,
    SimpleIssue,
)
from squid_i18n import md_tr

logger = logging.getLogger(__name__)


class SearchFieldRegistryProvider(Protocol):
    """Load the current public search-field catalog."""

    async def registry(self) -> FieldRegistry: ...


class SearchService:
    """Search builds, records, and metadata through the shared search index.

    The field registry defines the names and value types accepted in queries and
    sorts. The service resolves both against it, so the backend receives field
    definitions rather than names. The backend filters indexed documents with
    the parsed query and orders the matches. Lexical ranking uses text matches;
    semantic mode adds similarity between model-generated vectors to those
    lexical results.

    Query vectors are generated before opening the search transaction, so a slow
    model call does not hold a database connection. Filter-only queries and explicit
    field sorts do not need a vector.

    Record holders are replaced with current public build summaries, loaded once
    per page, so they match the records API; holders that are not public are dropped.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[SearchUnitOfWork],
        builds: PublicBuildSummaryReader,
        embedding_model: SearchEmbeddingModel | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._builds = builds
        self._embedding_model = embedding_model

    @application_operation
    async def search(self, request: SearchRequest, *, context: ActingContext) -> SearchPage:
        """Parse the request, fetch results, and add offset-based page links.

        An unknown or unsortable sort field raises ValidationError.

        Semantic searches generate their query vector before opening the search
        transaction. An embedding failure is logged and leaves the backend to
        return lexical results with a warning.
        """
        registry = (
            await self.fields(context=context) if request.parsed_query is None or request.sort is not None else None
        )
        query: SearchQuery | None = request.parsed_query
        clauses: tuple[QueryClause, ...] = ()
        if query is None:
            assert registry is not None
            parser = SearchQueryParser(registry, scope=request.scope)
            query = parser.parse(request.query)
            clauses = parser.clauses(query)
        sort_field = None
        if request.sort is not None:
            assert registry is not None
            sort_field = registry.resolve(request.sort.field)
            if sort_field is None or not sort_field.supports_sort:
                field_name = request.sort.field
                allowed = sorted(field.name for field in registry.definitions if field.supports_sort)
                raise ValidationError(
                    md_tr(t"search-search-results-sorted = Search results cannot be sorted by {field_name}."),
                    code=ErrorCode.INVALID_QUERY,
                    issues=(SimpleIssue(field="/query/sort", code=FieldErrorCode.UNKNOWN_OPTION),),
                    public_context={"allowed": allowed},
                )
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
            result = await uow.search.search(request, query, sort_field=sort_field, query_embedding=query_embedding)
        offset = request.offset if result.offset is None else result.offset
        following = offset + request.page_size
        return SearchPage(
            hits=await self._with_holders(result.hits, context=context),
            total=result.total,
            next=PageAnchor(offset=following) if following < result.total else None,
            prev=PageAnchor(offset=max(offset - request.page_size, 0)) if offset else None,
            warnings=result.warnings,
            offset=offset,
            clauses=clauses,
        )

    async def _with_holders(self, hits: tuple[SearchHit, ...], *, context: ActingContext) -> tuple[SearchHit, ...]:
        records = [
            record
            for hit in hits
            for record in (hit.records if isinstance(hit, BuildSearchHit) else (hit,))
            if isinstance(record, RecordSearchHit)
        ]
        holder_ids = tuple(dict.fromkeys(id for record in records for id in record.holder_build_ids))
        if not holder_ids:
            return hits
        builds = {build.id: build for build in await self._builds.get_public_summaries(holder_ids, context=context)}

        def summarize(record: RecordSearchHit) -> RecordSearchHit:
            holders = tuple(
                RecordHolderSummary.from_build(builds[id]) for id in record.holder_build_ids if id in builds
            )
            return replace(record, holders=holders)

        return tuple(
            replace(hit, records=tuple(summarize(record) for record in hit.records))
            if isinstance(hit, BuildSearchHit)
            else summarize(hit)
            if isinstance(hit, RecordSearchHit)
            else hit
            for hit in hits
        )

    @application_operation
    async def counts(self, request: SearchRequest, *, context: ActingContext) -> dict[SearchScope, int | None]:
        """Count the matches of `request.query` in every scope.

        Counts ignore grouping and the ranked candidate limit, so they can exceed
        a page's total. A scope that cannot express the query counts None.
        """
        async with self._uow_factory() as uow:
            registry = await uow.search_fields.registry()
            counts: dict[SearchScope, int | None] = {}
            for scope in SearchScope:
                try:
                    query = SearchQueryParser(registry, scope=scope).parse(request.query)
                except ValidationError:
                    counts[scope] = None
                    continue
                counts[scope] = await uow.search.count(replace(request, scope=scope), query)
            return counts

    @application_operation
    async def suggest(self, query: str | SearchQuery, *, limit: int = 5, context: ActingContext) -> tuple[str, ...]:
        """Suggest indexed titles; limit must be between 1 and 25."""
        if not 1 <= limit <= 25:
            msg = "suggestion limit must be between 1 and 25"
            raise ValidationError(
                msg,
                issues=(
                    BelowMinimum(field="limit", params=BelowMinimumParams(minimum=1))
                    if limit < 1
                    else AboveMaximum(field="limit", params=AboveMaximumParams(maximum=25)),
                ),
            )
        async with self._uow_factory() as uow:
            parsed = (
                SearchQueryParser(await uow.search_fields.registry()).parse(query) if isinstance(query, str) else query
            )
            return await uow.search.suggest(parsed, limit=limit)

    @application_operation
    async def fields(self, *, context: ActingContext) -> FieldRegistry:
        """Return the field registry used by query parsing."""
        async with self._uow_factory() as uow:
            return await uow.search_fields.registry()


class SearchUnitOfWork(UnitOfWork, Protocol):
    """Read search documents and the field catalog in one transaction."""

    @property
    def search(self) -> SearchBackend: ...

    @property
    def search_fields(self) -> SearchFieldRegistryProvider: ...
