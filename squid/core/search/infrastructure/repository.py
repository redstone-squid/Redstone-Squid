"""PostgreSQL search backend."""

import logging
from collections.abc import Callable, Sequence
from typing import override

from sqlalchemy import Integer, Text, cast, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from squid.core.search.application import (
    RankedCandidate,
    RankingBranch,
    SearchBackend,
    SearchSlice,
    is_filter_only,
    positive_text_expressions,
    reciprocal_rank_fusion,
)
from squid.core.search.application.ports import QueryEmbedding
from squid.core.search.application.query_analysis import close_match_terms, mentions_field
from squid.core.search.domain import (
    FieldDefinition,
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
    TextExpression,
)
from squid.core.search.domain.query import expression_applies_to
from squid.core.search.domain.text import TermExpansion, close_distance, terms
from squid.core.search.infrastructure.compiler import compile_query, rank_query
from squid.core.search.infrastructure.results import ResultOrder, ResultPager
from squid.core.search.models import SearchDocument, SearchTerm

_CANDIDATE_LIMIT = 200
_PUBLIC_BUILD_STATUSES = frozenset({"approved"})
_SEMANTIC_WARNING = "Semantic search is temporarily unavailable; showing lexical results."
logger = logging.getLogger(__name__)


class PostgresSearchBackend(SearchBackend):
    """Filter search documents and choose the order in which ResultPager pages them.

    Unquoted words also match indexed close spellings, looked up once per search.
    An explicit field sort orders all matches by that field. With no sort and no
    positive text to rank, matches use title order. Otherwise, a heading match and
    a weighted full-text rank each collect a bounded list of candidates, and record
    searches add records whose heading contains every word; semantic mode adds a
    vector-similarity list. Reciprocal rank fusion combines their positions into a
    relevance score.

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
        session = self._session()
        expansion = await self._expansion(session, query)
        predicate = self.compile_predicate(request, query, expansion)
        order: ResultOrder = sort_field
        warnings: tuple[str, ...] = ()
        if sort_field is None and not is_filter_only(query):
            order, warnings = await self._ranked(session, request, query, predicate, expansion, query_embedding)
        return await ResultPager(session, request, query, expansion).page(predicate, order, warnings=warnings)

    @override
    async def count(self, request: SearchRequest, query: SearchQuery) -> int:
        session = self._session()
        predicate = self.compile_predicate(request, query, await self._expansion(session, query))
        return await session.scalar(select(func.count(SearchDocument.id)).where(predicate)) or 0

    def compile_predicate(
        self, request: SearchRequest, query: SearchQuery, expansion: TermExpansion | None = None
    ) -> ColumnElement[bool]:
        """Compile scope, visibility policy, and the complete user predicate.

        Retired records only match a query that mentions `record_state`.
        """
        predicate = self._scope_predicate(request.scope) & compile_query(query, expansion or TermExpansion())
        if request.scope in {SearchScope.RECORDS, SearchScope.ALL} and not mentions_field(query, "record_state"):
            predicate &= or_(SearchDocument.resource_kind != "record", SearchDocument.status == "current")
        visible_statuses = request.visible_statuses
        if visible_statuses is None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            visible_statuses = _PUBLIC_BUILD_STATUSES
        if visible_statuses is not None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            predicate &= _visible_builds(visible_statuses)
        return predicate

    @override
    async def suggest(self, query: SearchQuery, *, limit: int) -> tuple[str, ...]:
        """Suggest close indexed titles using trigram similarity."""
        text = _ranking_text(query)
        if not text:
            return ()
        session = self._session()
        similarity = func.similarity(SearchDocument.normalized_title, text)
        statement = (
            select(SearchDocument.title)
            .where(_visible_builds(_PUBLIC_BUILD_STATUSES), similarity > 0.1)
            .order_by(similarity.desc(), SearchDocument.normalized_title)
            .limit(limit)
        )
        return tuple((await session.scalars(statement)).all())

    async def _expansion(self, session: AsyncSession, query: SearchQuery) -> TermExpansion:
        """Find indexed close spellings of the query's unquoted words in one lookup."""
        words = sorted(close_match_terms(query))
        if not words:
            return TermExpansion()
        requested = select(
            func.unnest(cast(words, ARRAY(Text))).label("word"),
            func.unnest(cast([close_distance(word) for word in words], ARRAY(Integer))).label("edits"),
        ).subquery()
        rows = await session.execute(
            select(requested.c.word, SearchTerm.term).join(
                SearchTerm,
                (SearchTerm.term != requested.c.word)
                & (func.abs(func.length(SearchTerm.term) - func.length(requested.c.word)) <= requested.c.edits)
                & (
                    func.levenshtein_less_equal(SearchTerm.term, requested.c.word, requested.c.edits)
                    <= requested.c.edits
                ),
            )
        )
        close: dict[str, set[str]] = {}
        for word, term in rows.tuples():
            close.setdefault(word, set()).add(term)
        return TermExpansion({word: frozenset(found) for word, found in close.items()})

    async def _ranked(
        self,
        session: AsyncSession,
        request: SearchRequest,
        query: SearchQuery,
        predicate: ColumnElement[bool],
        expansion: TermExpansion,
        query_embedding: QueryEmbedding | None,
    ) -> tuple[dict[int, RankedCandidate], tuple[str, ...]]:
        """Fuse the ranking branches into scored candidates by document ID, with any warnings.

        Each branch contributes at most `_CANDIDATE_LIMIT` documents. Only these
        candidates are paged, so pagination ends at the documents we actually
        ranked even when more match the filters.
        """
        text = _ranking_text(query)
        # Record shorthand parses the leading base title as a filter. Keep it in
        # the heading comparison so "fastest 2x2 door" favors the general record.
        record_query = request.scope is SearchScope.RECORDS or (
            request.scope is SearchScope.ALL
            and query.expression is not None
            and expression_applies_to(query.expression, SearchScope.RECORDS)
            and not expression_applies_to(query.expression, SearchScope.BUILDS)
        )
        heading_terms = sorted(set(terms(request.query if record_query else text)))
        branches: dict[RankingBranch, Sequence[RankedCandidate]] = {
            RankingBranch.EXACT: await self._candidate_rows(
                session, predicate & (SearchDocument.heading_terms == heading_terms), literal(1.0)
            ),
        }
        # Rank by exact terms; documents matched only through close spellings come last.
        if (ranking := rank_query(text)) is not None:
            branches[RankingBranch.FULL_TEXT] = await self._candidate_rows(
                session, predicate, func.ts_rank_cd(SearchDocument.search_vector, ranking)
            )
        if record_query and heading_terms:
            # A holder can match the same words as a category. Prefer records whose own
            # heading has every word, and among those the most general category.
            branches[RankingBranch.RECORD_TITLE] = await self._candidate_rows(
                session,
                predicate
                & (SearchDocument.resource_kind == "record")
                & SearchDocument.heading_terms.contains(heading_terms),
                -func.cardinality(SearchDocument.heading_terms),
            )
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

    async def _candidate_rows(
        self,
        session: AsyncSession,
        predicate: ColumnElement[bool],
        score: ColumnElement[float],
    ) -> tuple[RankedCandidate, ...]:
        statement = (
            select(SearchDocument.id, score.label("score"))
            .where(predicate)
            .order_by(score.desc(), SearchDocument.resource_kind, SearchDocument.source_key)
            .limit(_CANDIDATE_LIMIT)
        )
        return tuple(
            RankedCandidate(document_id, float(value)) for document_id, value in (await session.execute(statement))
        )

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
            select(SearchDocument.id, nearest.c.distance)
            .join(nearest, SearchDocument.id == nearest.c.id)
            .where(predicate)
            .order_by(nearest.c.distance)
        )
        return tuple(
            RankedCandidate(document_id, -float(distance)) for document_id, distance in await session.execute(statement)
        )

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
