"""PostgreSQL search backend."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast, override

from sqlalchemy import case, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from squid.core.errors import ErrorCode, ValidationError
from squid.core.i18n import _
from squid.search.application import (
    RankedCandidate,
    RankingBranch,
    SearchBackend,
    SearchFieldRegistryProvider,
    SearchSlice,
    is_filter_only,
    positive_text_expressions,
    reciprocal_rank_fusion,
)
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldDefinition, FieldType
from squid.search.domain import (
    BuildSearchHit,
    MetadataSearchHit,
    RecordSearchHit,
    SearchHit,
    SearchMode,
    SearchQuery,
    SearchRequest,
    SearchScope,
    SortDirection,
    TextExpression,
)
from squid.search.infrastructure.compiler import PostgresSearchQueryCompiler
from squid.search.infrastructure.models import SearchDocument, SearchDocumentFacet

_CANDIDATE_LIMIT = 200
_SEMANTIC_WARNING = "Semantic search is temporarily unavailable; showing lexical results."
ResourceKind = Literal["record", "build", "metadata"]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    """A semantic provider result ordered from most to least relevant."""

    resource_kind: str
    source_key: str


class SemanticCandidateProvider(Protocol):
    """Optional vector-query provider."""

    async def candidates(self, query: str, *, limit: int) -> Sequence[SemanticCandidate]: ...


class PostgresSearchBackend(SearchBackend):
    """Execute filtered lexical search against indexed projection documents."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        compiler: PostgresSearchQueryCompiler | None = None,
        semantic_provider: SemanticCandidateProvider | None = None,
        fields: SearchFieldRegistryProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._compiler = compiler or PostgresSearchQueryCompiler()
        self._semantic_provider = semantic_provider
        self._fields = fields

    @override
    async def search(
        self,
        request: SearchRequest,
        query: SearchQuery,
        *,
        offset: int,
    ) -> SearchSlice:
        """Search indexed documents and return the window starting at `offset`."""
        registry = DEFAULT_FIELD_REGISTRY if self._fields is None else await self._fields.registry()
        compiler = self._compiler if self._fields is None else PostgresSearchQueryCompiler(registry)
        predicate = self.compile_predicate(request, query, compiler=compiler)
        async with self._session_factory() as session:
            if request.sort is not None:
                field = registry.resolve(request.sort.field)
                if field is None or not field.supports_sort:
                    # User input on both transports, so a message rather than a 500.
                    msg = _("Search results cannot be sorted by {field_name}.")
                    raise ValidationError(
                        msg,
                        code=ErrorCode.INVALID_QUERY,
                        message_params={"field_name": request.sort.field},
                        public_context={"field": "sort", "value": request.sort.field},
                    )
                return await self._sorted(session, request, predicate, offset, field)
            if is_filter_only(query):
                return await self._filter_only(session, request, predicate, offset)
            return await self._ranked(session, request, query, predicate, offset)

    def compile_predicate(
        self,
        request: SearchRequest,
        query: SearchQuery,
        *,
        compiler: PostgresSearchQueryCompiler | None = None,
    ) -> ColumnElement[bool]:
        """Compile scope, visibility policy, and the complete user predicate."""
        predicate = self._scope_predicate(request.scope) & (compiler or self._compiler).compile(query)
        visible_statuses = request.visible_statuses
        if visible_statuses is None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            visible_statuses = frozenset({"confirmed"})
        if visible_statuses is not None and request.scope in {SearchScope.BUILDS, SearchScope.ALL}:
            predicate &= or_(
                SearchDocument.resource_kind != "build",
                func.lower(SearchDocument.status).in_(sorted(status.casefold() for status in visible_statuses)),
            )
        return predicate

    async def _sorted(
        self,
        session: AsyncSession,
        request: SearchRequest,
        predicate: ColumnElement[bool],
        offset: int,
        field: FieldDefinition,
    ) -> SearchSlice:
        """Page documents by one scalar facet field, with no depth limit.

        A document may hold several values for the same field -- facets are unique per
        `(document_id, field_name, ordinal)` -- so joining the facet table directly would repeat
        the document once per value and put OFFSET arithmetic out by however many duplicates fell
        before the window. Aggregating to one row per document first makes the offset exact, and
        picks the value a caller would expect to sort on: the smallest ascending, the largest
        descending.
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
        async with self._session_factory() as session:
            statement = (
                select(SearchDocument.title)
                .where(func.similarity(SearchDocument.fuzzy_text, terms) > 0.1)
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
    ) -> SearchSlice:
        """Page the fused relevance ranking.

        `total` is the size of the fused candidate list, not of the corpus: ranking materializes at
        most `_CANDIDATE_LIMIT` candidates per branch, so a query matching more documents than that
        reports the capped figure and runs out of pages there. Deep relevance pages are not useful
        enough to justify ranking the whole corpus, and an honest end beats a silently empty page.
        """
        terms = _ranking_text(query)
        branches: dict[RankingBranch, Sequence[RankedCandidate]] = {
            RankingBranch.EXACT: await self._exact_candidates(session, predicate, terms),
            RankingBranch.FULL_TEXT: await self._fts_candidates(session, predicate, terms),
            RankingBranch.TRIGRAM: await self._trigram_candidates(session, predicate, terms),
        }
        warnings: tuple[str, ...] = ()
        if request.mode is SearchMode.SEMANTIC:
            if self._semantic_provider is None:
                warnings = (_SEMANTIC_WARNING,)
            else:
                try:
                    branches[RankingBranch.SEMANTIC] = await self._semantic_candidates(
                        session, predicate, request.query
                    )
                except Exception:
                    logger.exception("Semantic search candidate generation failed")
                    warnings = (_SEMANTIC_WARNING,)
        ranked = reciprocal_rank_fusion(branches)
        page_candidates = ranked[offset : offset + request.page_size]
        documents = await self._documents_by_candidate(session, page_candidates)
        hits = tuple(
            _to_hit(documents[(candidate.resource_kind, candidate.source_id)], candidate.score)
            for candidate in page_candidates
            if (candidate.resource_kind, candidate.source_id) in documents
        )
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
        """Count matching documents.

        The compiled predicate stands alone -- it never reaches through a join -- so a total needs
        no facet join even when the page it accompanies was ordered through one.
        """
        return await session.scalar(select(func.count()).select_from(SearchDocument).where(predicate)) or 0

    async def _exact_candidates(
        self, session: AsyncSession, predicate: ColumnElement[bool], terms: str
    ) -> tuple[RankedCandidate, ...]:
        score = case((SearchDocument.normalized_title == terms.casefold(), 1.0), else_=0.0)
        return await self._candidate_rows(
            session,
            predicate & (SearchDocument.normalized_title == terms.casefold()),
            score,
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
            select(SearchDocument.resource_kind, SearchDocument.source_key, SearchDocument.normalized_title)
            .where(predicate)
            .order_by(score.desc(), SearchDocument.resource_kind, SearchDocument.source_key)
            .limit(_CANDIDATE_LIMIT)
        )
        rows = (await session.execute(statement)).all()
        return tuple(
            RankedCandidate(
                source_id=_public_source_id(row.resource_kind, row.source_key),
                resource_kind=_group_kind(row.resource_kind),
                normalized_title=row.normalized_title,
            )
            for row in rows
        )

    async def _semantic_candidates(
        self,
        session: AsyncSession,
        predicate: ColumnElement[bool],
        query: str,
    ) -> tuple[RankedCandidate, ...]:
        if self._semantic_provider is None:
            return ()
        candidates = tuple(await self._semantic_provider.candidates(query, limit=_CANDIDATE_LIMIT))
        if not candidates:
            return ()
        identities = tuple((candidate.resource_kind, candidate.source_key) for candidate in candidates)
        identity_predicate = or_(
            *(
                (SearchDocument.resource_kind == resource_kind) & (SearchDocument.source_key == source_key)
                for resource_kind, source_key in identities
            )
        )
        statement = select(SearchDocument).where(predicate, identity_predicate)
        allowed = {
            (document.resource_kind, document.source_key): document
            for document in (await session.scalars(statement)).all()
        }
        return tuple(
            RankedCandidate(
                _public_source_id(candidate.resource_kind, candidate.source_key),
                _group_kind(candidate.resource_kind),
                allowed[(candidate.resource_kind, candidate.source_key)].normalized_title,
            )
            for candidate in candidates
            if (candidate.resource_kind, candidate.source_key) in allowed
        )

    async def _documents_by_candidate(
        self,
        session: AsyncSession,
        candidates: Sequence[RankedCandidate],
    ) -> dict[tuple[ResourceKind, str], SearchDocument]:
        if not candidates:
            return {}
        identities = tuple(_raw_identity(candidate.resource_kind, candidate.source_id) for candidate in candidates)
        predicate = or_(
            *(
                (SearchDocument.resource_kind == resource_kind) & (SearchDocument.source_key == source_key)
                for resource_kind, source_key in identities
            )
        )
        documents = (await session.scalars(select(SearchDocument).where(predicate))).all()
        return {
            (
                _group_kind(document.resource_kind),
                _public_source_id(document.resource_kind, document.source_key),
            ): document
            for document in documents
        }

    @staticmethod
    def _scope_predicate(scope: SearchScope) -> ColumnElement[bool]:
        if scope is SearchScope.RECORDS:
            return SearchDocument.resource_kind == "record"
        if scope is SearchScope.BUILDS:
            return SearchDocument.resource_kind == "build"
        if scope is SearchScope.METADATA:
            return SearchDocument.resource_kind.not_in(("record", "build"))
        return true()


def _ranking_text(query: SearchQuery) -> str:
    values: list[str] = []
    for expression in positive_text_expressions(query):
        if isinstance(expression, TextExpression):
            values.append(expression.value)
        elif not isinstance(expression.value, bool | int | float):
            values.append(str(expression.value))
    return " ".join(values)


def _group_kind(resource_kind: str) -> ResourceKind:
    if resource_kind in {"record", "build"}:
        return cast(ResourceKind, resource_kind)
    return "metadata"


def _public_source_id(resource_kind: str, source_key: str) -> str:
    if resource_kind in {"record", "build"}:
        return source_key
    return f"{resource_kind}:{source_key}"


def _raw_identity(resource_kind: ResourceKind, source_id: str) -> tuple[str, str]:
    if resource_kind != "metadata":
        return resource_kind, source_id
    raw_kind, separator, source_key = source_id.partition(":")
    return (raw_kind, source_key) if separator else ("metadata", source_id)


def _to_hit(document: SearchDocument, score: float | None) -> SearchHit:
    data = document.document_data
    tags = tuple(document.tags)
    if document.resource_kind == "record":
        metrics = data.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        return RecordSearchHit(
            source_id=document.source_key,
            title=document.title,
            subtitle=document.subtitle,
            build_id=_integer(data, "build_id"),
            build_title=_string(data, "build_title", document.title),
            record_class=_string(data, "record_class", "unknown"),
            version_scope=_string(data, "version_scope", "all-time"),
            score=score,
            tags=tags,
            metrics=cast(dict[str, str | int | float | bool], metrics),
        )
    if document.resource_kind == "build":
        return BuildSearchHit(
            source_id=document.source_key,
            title=document.title,
            status=document.status or "unknown",
            description=document.description,
            score=score,
            tags=tags,
        )
    aliases = data.get("aliases", ())
    if not isinstance(aliases, list | tuple):
        aliases = ()
    typed_aliases = cast(list[object] | tuple[object, ...], aliases)
    return MetadataSearchHit(
        source_id=_public_source_id(document.resource_kind, document.source_key),
        title=document.title,
        metadata_kind=_string(data, "metadata_kind", document.resource_kind),
        description=document.description,
        score=score,
        aliases=tuple(str(alias) for alias in typed_aliases),
    )


def _string(data: dict[str, object], key: str, default: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default


def _integer(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
