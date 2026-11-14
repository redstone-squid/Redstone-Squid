"""PostgreSQL search backend."""

import logging
from collections.abc import Callable, Sequence
from typing import override

from sqlalchemy import func, literal, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from squid.search.application import (
    RankedCandidate,
    RankingBranch,
    SearchBackend,
    SearchSlice,
    is_filter_only,
    positive_text_expressions,
    reciprocal_rank_fusion,
)
from squid.search.application.ports import QueryEmbedding
from squid.search.domain import (
    FieldDefinition,
    FieldType,
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
    SortDirection,
    TextExpression,
)
from squid.search.infrastructure.compiler import compile_query
from squid.search.infrastructure.grouping import GroupedSearch
from squid.search.infrastructure.hits import document_hit as _to_hit
from squid.search.models import SearchDocument, SearchDocumentFacet

_CANDIDATE_LIMIT = 200
_PUBLIC_BUILD_STATUSES = frozenset({"confirmed"})
_SEMANTIC_WARNING = "Semantic search is temporarily unavailable; showing lexical results."
logger = logging.getLogger(__name__)


class PostgresSearchBackend(SearchBackend):
    """Filter search documents and choose their order for the requested page.

    An explicit field sort orders all matches by that field. With no sort and no
    positive text to rank, matches use title order. Otherwise, exact-title,
    full-text, and trigram queries each collect a bounded list of candidates;
    semantic mode adds a vector-similarity list. Reciprocal rank fusion combines
    their positions into the final order.

    Every branch applies the complete query and visibility filters. Ranked totals
    count the combined candidate pool, while sorted and filter-only totals count
    all matching documents.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    @override
    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        *,
        sort_field: FieldDefinition | None = None,
        query_embedding: QueryEmbedding | None = None,
    ) -> SearchSlice:
        """Search indexed documents and return the window starting at `request.offset`."""
        predicate = self.compile_predicate(request, query)
        session = self._session()
        offset = request.offset
        if sort_field is not None:
            if request.group_by_build:
                return await GroupedSearch(session, request, query).search(
                    predicate, compile_query(query), offset=offset, field=sort_field
                )
            return await self._sorted(session, request, predicate, offset, sort_field)
        if is_filter_only(query):
            if request.group_by_build:
                return await GroupedSearch(session, request, query).search(
                    predicate, compile_query(query), offset=offset
                )
            return await self._filter_only(session, request, predicate, offset)
        return await self._ranked(session, request, query, predicate, offset, query_embedding, compile_query(query))

    def compile_predicate(self, request: SearchRequest, query: SearchQuery) -> ColumnElement[bool]:
        """Compile scope, visibility policy, and the complete user predicate."""
        predicate = self._scope_predicate(request.scope) & compile_query(query)
        visible_statuses = request.visible_statuses
        if visible_statuses is None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            visible_statuses = _PUBLIC_BUILD_STATUSES
        if visible_statuses is not None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            predicate &= _visible_builds(visible_statuses)
        return predicate

    async def _sorted(
        self,
        session: AsyncSession,
        request: SearchRequest,
        predicate: ColumnElement[bool],
        offset: int,
        field: FieldDefinition,
    ) -> SearchSlice:
        """Page matches by a field value, putting documents without that field last.

        A document can have several values for a field. Use its smallest value for
        ascending order and its largest for descending order. Aggregating before
        joining also prevents duplicate documents from shifting page offsets.
        Unlike relevance ranking, this path does not cap the candidate list.
        """
        ascending = request.sort is None or request.sort.direction is SortDirection.ASCENDING
        value_source = {
            FieldType.TEXT: func.lower(SearchDocumentFacet.text_value),
            FieldType.NUMBER: SearchDocumentFacet.numeric_value,
            FieldType.TIMESTAMP: SearchDocumentFacet.timestamp_value,
            FieldType.BOOLEAN: SearchDocumentFacet.boolean_value,
        }[field.value_type]
        if field.value_type is FieldType.BOOLEAN:
            # PostgreSQL has no min/max over boolean; these are the same aggregate on false < true.
            aggregate = func.bool_and(value_source) if ascending else func.bool_or(value_source)
        else:
            aggregate = func.min(value_source) if ascending else func.max(value_source)
        anchor = (
            select(SearchDocumentFacet.document_id.label("document_id"), aggregate.label("sort_value"))
            .where(SearchDocumentFacet.field_name == (field.storage_name or field.name))
            .group_by(SearchDocumentFacet.document_id)
            .subquery("sort_anchor")
        )
        sort_value = anchor.c.sort_value
        statement = (
            select(SearchDocument)
            .outerjoin(anchor, anchor.c.document_id == SearchDocument.id)
            .where(predicate)
            .order_by(
                sort_value.is_(None),
                sort_value.asc() if ascending else sort_value.desc(),
                SearchDocument.normalized_title,
                SearchDocument.resource_kind,
                SearchDocument.source_key,
            )
            .offset(offset)
            .limit(request.page_size)
        )
        documents = (await session.scalars(statement)).unique().all()
        total = await self._count(session, predicate)
        return SearchSlice(tuple(_to_hit(document, None) for document in documents), total)

    @override
    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]:
        """Suggest close indexed titles using trigram similarity."""
        terms = _ranking_text(query)
        if not terms:
            return ()
        session = self._session()
        statement = (
            select(SearchDocument.title)
            .where(
                _visible_builds(_PUBLIC_BUILD_STATUSES),
                func.similarity(SearchDocument.fuzzy_text, terms) > 0.1,
            )
            .order_by(func.similarity(SearchDocument.fuzzy_text, terms).desc(), SearchDocument.normalized_title)
            .limit(limit)
        )
        return tuple((await session.scalars(statement)).all())

    async def _ranked(
        self,
        session: AsyncSession,
        request: SearchRequest,
        query: SearchQuery,
        predicate: ColumnElement[bool],
        offset: int,
        query_embedding: QueryEmbedding | None,
        query_predicate: ColumnElement[bool],
    ) -> SearchSlice:
        """Combine the ranking branches and return one page of candidates.

        Each branch contributes at most `_CANDIDATE_LIMIT` documents. Total counts
        their deduplicated union, so pagination ends at the candidates we actually
        ranked even when more documents match the filters.
        """
        terms = _ranking_text(query)
        branches: dict[RankingBranch, Sequence[RankedCandidate]] = {
            RankingBranch.EXACT: await self._exact_candidates(session, predicate, terms),
            RankingBranch.FULL_TEXT: await self._fts_candidates(session, predicate, terms),
            RankingBranch.TRIGRAM: await self._trigram_candidates(session, predicate, terms),
        }
        warnings: tuple[str, ...] = ()
        if request.mode is SearchMode.SEMANTIC:
            if query_embedding is None:
                warnings = (_SEMANTIC_WARNING,)
            else:
                try:
                    # A failed vector query must leave lexical results usable.
                    async with session.begin_nested():
                        branches[RankingBranch.SEMANTIC] = await self._semantic_candidates(
                            session, predicate, query_embedding
                        )
                except Exception:
                    logger.exception("Semantic search candidate lookup failed")
                    warnings = (_SEMANTIC_WARNING,)
        ranked = reciprocal_rank_fusion(branches)
        if request.group_by_build:
            candidates = {candidate.document_id: candidate for candidate in ranked}
            return await GroupedSearch(session, request, query).search(
                predicate, query_predicate, offset=offset, ranked=candidates, warnings=warnings
            )
        page_candidates = ranked[offset : offset + request.page_size]
        page_ids = [candidate.document_id for candidate in page_candidates]
        documents = {
            document.id: document
            for document in (await session.scalars(select(SearchDocument).where(SearchDocument.id.in_(page_ids)))).all()
        }
        hits = tuple(_to_hit(documents[candidate.document_id], candidate.score) for candidate in page_candidates)
        return SearchSlice(hits, len(ranked), warnings)

    async def _filter_only(
        self,
        session: AsyncSession,
        request: SearchRequest,
        predicate: ColumnElement[bool],
        offset: int,
    ) -> SearchSlice:
        """Page a query with nothing to rank by, ordered by title."""
        statement = (
            select(SearchDocument)
            .where(predicate)
            .order_by(
                SearchDocument.normalized_title,
                SearchDocument.resource_kind,
                SearchDocument.source_key,
            )
            .offset(offset)
            .limit(request.page_size)
        )
        documents = (await session.scalars(statement)).all()
        total = await self._count(session, predicate)
        return SearchSlice(tuple(_to_hit(document, None) for document in documents), total)

    @staticmethod
    async def _count(session: AsyncSession, predicate: ColumnElement[bool]) -> int:
        """Count matches without joining facets, which could count a document more than once."""
        return await session.scalar(select(func.count()).select_from(SearchDocument).where(predicate)) or 0

    async def _exact_candidates(
        self, session: AsyncSession, predicate: ColumnElement[bool], terms: str
    ) -> tuple[RankedCandidate, ...]:
        # Every exact match is equally good, so the candidate tie-break alone orders this branch.
        return await self._candidate_rows(
            session, predicate & (SearchDocument.normalized_title == terms.casefold()), literal(1.0)
        )

    async def _fts_candidates(
        self, session: AsyncSession, predicate: ColumnElement[bool], terms: str
    ) -> tuple[RankedCandidate, ...]:
        text_query = func.plainto_tsquery("simple", terms)
        score = func.ts_rank_cd(SearchDocument.combined_vector, text_query)
        return await self._candidate_rows(
            session,
            predicate & SearchDocument.combined_vector.bool_op("@@")(text_query),
            score,
        )

    async def _trigram_candidates(
        self, session: AsyncSession, predicate: ColumnElement[bool], terms: str
    ) -> tuple[RankedCandidate, ...]:
        score = func.similarity(SearchDocument.fuzzy_text, terms)
        return await self._candidate_rows(session, predicate & (score > 0.1), score)

    async def _candidate_rows(
        self,
        session: AsyncSession,
        predicate: ColumnElement[bool],
        score: ColumnElement[float],
    ) -> tuple[RankedCandidate, ...]:
        statement = (
            select(SearchDocument.id, SearchDocument.normalized_title)
            .where(predicate)
            .order_by(score.desc(), SearchDocument.resource_kind, SearchDocument.source_key)
            .limit(_CANDIDATE_LIMIT)
        )
        return tuple(RankedCandidate(row.id, row.normalized_title) for row in (await session.execute(statement)).all())

    async def _semantic_candidates(
        self,
        session: AsyncSession,
        predicate: ColumnElement[bool],
        embedding: QueryEmbedding,
    ) -> tuple[RankedCandidate, ...]:
        distance = SearchDocument.embedding.cosine_distance(embedding.vector)
        # Choose the nearest documents across the index first, then apply filters.
        # A restrictive filter can therefore leave fewer than the candidate limit.
        nearest = (
            select(SearchDocument.id, distance.label("distance"))
            .where(SearchDocument.embedding.is_not(None), SearchDocument.embedding_model == embedding.model)
            .order_by(distance)
            .limit(_CANDIDATE_LIMIT)
            .subquery()
        )
        statement = (
            select(SearchDocument.id, SearchDocument.normalized_title)
            .join(nearest, SearchDocument.id == nearest.c.id)
            .where(predicate)
            .order_by(nearest.c.distance)
        )
        return tuple(RankedCandidate(row.id, row.normalized_title) for row in (await session.execute(statement)).all())

    @staticmethod
    def _scope_predicate(scope: SearchScope) -> ColumnElement[bool]:
        if scope is SearchScope.RECORDS:
            return SearchDocument.resource_kind == "record"
        if scope is SearchScope.BUILDS:
            return SearchDocument.resource_kind == "build"
        if scope is SearchScope.METADATA:
            return SearchDocument.resource_kind.not_in(("record", "build"))
        return true()


def _visible_builds(statuses: frozenset[str]) -> ColumnElement[bool]:
    """Restrict build statuses while leaving records and metadata eligible."""
    return or_(
        SearchDocument.resource_kind != "build",
        func.lower(SearchDocument.status).in_(sorted(status.casefold() for status in statuses)),
    )


def _ranking_text(query: SearchQuery) -> str:
    values: list[str] = []
    for expression in positive_text_expressions(query):
        if isinstance(expression, TextExpression):
            values.append(expression.value)
        elif not isinstance(expression.value, bool | int | float):
            values.append(str(expression.value))
    return " ".join(values)
