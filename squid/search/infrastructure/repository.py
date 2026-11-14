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
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
    TextExpression,
)
from squid.search.infrastructure.compiler import compile_query
from squid.search.infrastructure.results import ResultOrder, ResultPager
from squid.search.models import SearchDocument

_CANDIDATE_LIMIT = 200
_PUBLIC_BUILD_STATUSES = frozenset({"confirmed"})
_SEMANTIC_WARNING = "Semantic search is temporarily unavailable; showing lexical results."
logger = logging.getLogger(__name__)


class PostgresSearchBackend(SearchBackend):
    """Filter search documents and choose the order in which ResultPager pages them.

    An explicit field sort orders all matches by that field. With no sort and no
    positive text to rank, matches use title order. Otherwise, exact-title,
    full-text, and trigram queries each collect a bounded list of candidates;
    semantic mode adds a vector-similarity list. Reciprocal rank fusion combines
    their positions into a relevance score.

    Every branch applies the complete query and visibility filters. Ranked totals
    count the combined candidate pool, while sorted and filter-only totals count
    everything that matches.
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
        order: ResultOrder = sort_field
        warnings: tuple[str, ...] = ()
        if sort_field is None and not is_filter_only(query):
            order, warnings = await self._ranked(session, request, query, predicate, query_embedding)
        return await ResultPager(session, request, query).page(predicate, order, warnings=warnings)

    def compile_predicate(self, request: SearchRequest, query: SearchQuery) -> ColumnElement[bool]:
        """Compile scope, visibility policy, and the complete user predicate."""
        predicate = self._scope_predicate(request.scope) & compile_query(query)
        visible_statuses = request.visible_statuses
        if visible_statuses is None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            visible_statuses = _PUBLIC_BUILD_STATUSES
        if visible_statuses is not None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            predicate &= _visible_builds(visible_statuses)
        return predicate

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
        query_embedding: QueryEmbedding | None,
    ) -> tuple[dict[int, RankedCandidate], tuple[str, ...]]:
        """Fuse the ranking branches into scored candidates by document ID, with any warnings.

        Each branch contributes at most `_CANDIDATE_LIMIT` documents. Only these
        candidates are paged, so pagination ends at the documents we actually
        ranked even when more match the filters.
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
        return {candidate.document_id: candidate for candidate in reciprocal_rank_fusion(branches)}, warnings

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
            select(SearchDocument.id)
            .where(predicate)
            .order_by(score.desc(), SearchDocument.resource_kind, SearchDocument.source_key)
            .limit(_CANDIDATE_LIMIT)
        )
        return tuple(RankedCandidate(document_id) for document_id in (await session.scalars(statement)).all())

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
            select(SearchDocument.id)
            .join(nearest, SearchDocument.id == nearest.c.id)
            .where(predicate)
            .order_by(nearest.c.distance)
        )
        return tuple(RankedCandidate(document_id) for document_id in (await session.scalars(statement)).all())

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
