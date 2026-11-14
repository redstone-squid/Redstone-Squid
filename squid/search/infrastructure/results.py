"""Turn the documents that match a search into one ordered page of results.

A result unit is what one row of a results page stands for. Without grouping,
every matching document is its own unit. With `SearchRequest.group_by_build`, a
matching record is replaced by the builds that hold it, so a build that holds
several matching records, or matches in its own right as well, appears once.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from sqlalchemy import case, func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from squid.search.application.highlighting import highlight_title
from squid.search.application.ports import SearchSlice
from squid.search.application.ranking import RankedCandidate
from squid.search.domain import (
    BuildSearchHit,
    FieldDefinition,
    FieldType,
    MetadataSearchHit,
    RecordSearchHit,
    SearchHit,
    SearchMatch,
    SearchQuery,
    SearchRequest,
    SearchScope,
    SearchText,
    SortDirection,
)
from squid.search.infrastructure.compiler import compile_query
from squid.search.infrastructure.hits import document_hit, public_identity
from squid.search.models import SearchDocument, SearchDocumentFacet

type ResultOrder = Mapping[int, RankedCandidate] | FieldDefinition | None
"""How result units are ordered.

Relevance candidates by document ID order units by their best fused score and
leave out documents that are not candidates. A field definition sorts by that
field in the direction of `SearchRequest.sort`. None uses title order.
"""


class ResultPager:
    """Map matching documents to result units, page the units in SQL, and load their hits.

    Units are counted, ordered, and sliced before any document is loaded, so the
    total and the offsets refer to units rather than to matching documents. Ties
    in the requested order are broken by title and then by the unit's document ID.

    When grouping, holder facets connect records to build documents. Visibility
    applies to each holder, including ties, and a record without a visible holder
    produces no result. Record details are loaded separately for the page's
    builds, so a bounded relevance pool does not truncate a build's records.
    """

    def __init__(self, session: AsyncSession, request: SearchRequest, query: SearchQuery) -> None:
        self.session = session
        self.request = request
        self.query = query
        self.statuses = request.visible_statuses if request.visible_statuses is not None else frozenset({"confirmed"})

    async def page(
        self, predicate: ColumnElement[bool], order: ResultOrder, *, warnings: tuple[str, ...] = ()
    ) -> SearchSlice:
        """Return the units starting at `request.offset` among documents matching `predicate`."""
        ranked = None if order is None or isinstance(order, FieldDefinition) else order
        if ranked is not None and not ranked:
            return SearchSlice((), 0, warnings)
        matches = self._units(predicate)
        units = matches
        if ranked is not None:
            units = select(matches).where(matches.c.document_id.in_(ranked)).cte("ranked_units")
        statement = select(units.c.unit_id, func.min(units.c.unit_title).label("title")).group_by(units.c.unit_id)
        if ranked is not None:
            # A unit ranks as its best document, so holding more matching records does not boost a build.
            score = case({key: value.score for key, value in ranked.items()}, value=units.c.document_id, else_=0.0)
            statement = statement.add_columns(func.max(score).label("order_value"))
        elif isinstance(order, FieldDefinition):
            statement = statement.outerjoin(
                SearchDocumentFacet,
                (SearchDocumentFacet.document_id == units.c.document_id)
                & (SearchDocumentFacet.field_name == (order.storage_name or order.name)),
            ).add_columns(self._sort_value(order).label("order_value"))
        paged = statement.cte("result_units")
        total = await self.session.scalar(select(func.count()).select_from(paged)) or 0
        ordering = [paged.c.title, paged.c.unit_id]
        if ranked is not None:
            ordering = [paged.c.order_value.desc(), *ordering]
        elif order is not None:
            value = paged.c.order_value
            # Units without the sort field come last in either direction.
            ordering = [value.is_(None), value.desc() if self._descending else value.asc(), *ordering]
        rows = (
            await self.session.execute(
                select(paged).order_by(*ordering).offset(self.request.offset).limit(self.request.page_size)
            )
        ).all()
        if not rows:
            return SearchSlice((), total, warnings)
        unit_ids = [row.unit_id for row in rows]
        documents = {
            document.id: document
            for document in (
                await self.session.scalars(select(SearchDocument).where(SearchDocument.id.in_(unit_ids)))
            ).all()
        }
        scores = {row.unit_id: float(row.order_value) if ranked is not None else None for row in rows}
        if not self.request.group_by_build:
            hits = tuple(document_hit(documents[unit_id], scores[unit_id]) for unit_id in unit_ids)
            return SearchSlice(hits, total, warnings)
        # Use every match rather than the ranked ones, so a hit also lists matches outside the candidate pool.
        hits = await self._grouped_hits(matches, unit_ids, documents, scores, ranked or {})
        return SearchSlice(hits, total, warnings)

    @property
    def _descending(self) -> bool:
        return self.request.sort is not None and self.request.sort.direction is SortDirection.DESCENDING

    def _sort_value(self, field: FieldDefinition) -> ColumnElement[Any]:
        """Aggregate a unit's values for the sort field into the one it is ordered by.

        A unit can have several values, either as repeated facets of one document
        or across the records of a build. Ascending order uses the smallest and
        descending order the largest. Aggregating also keeps a unit from appearing
        once per value, which would shift page offsets.
        """
        value = {
            FieldType.TEXT: func.lower(SearchDocumentFacet.text_value),
            FieldType.NUMBER: SearchDocumentFacet.numeric_value,
            FieldType.TIMESTAMP: SearchDocumentFacet.timestamp_value,
            FieldType.BOOLEAN: SearchDocumentFacet.boolean_value,
        }[field.value_type]
        if field.value_type is FieldType.BOOLEAN:
            # PostgreSQL has no min/max over boolean; these are the same aggregates on false < true.
            return func.bool_or(value) if self._descending else func.bool_and(value)
        return func.max(value) if self._descending else func.min(value)

    async def _grouped_hits(
        self,
        matches: CTE,
        unit_ids: list[int],
        documents: Mapping[int, SearchDocument],
        scores: Mapping[int, float | None],
        ranked: Mapping[int, RankedCandidate],
    ) -> tuple[SearchHit, ...]:
        """Load build and metadata hits with the documents that matched and the builds' records."""
        contributions: dict[int, list[SearchDocument]] = defaultdict(list)
        rows = (
            await self.session.execute(
                select(matches.c.unit_id, SearchDocument)
                .join(SearchDocument, SearchDocument.id == matches.c.document_id)
                .where(matches.c.unit_id.in_(unit_ids))
                .distinct()
            )
        ).all()
        for unit_id, document in rows:
            contributions[unit_id].append(document)
        records = await self._records(documents, compile_query(self.query), ranked)
        hits: list[SearchHit] = []
        for unit_id in unit_ids:
            hit = document_hit(documents[unit_id], scores[unit_id])
            sources = tuple(
                self._match(source, ranked.get(source.id))
                for source in sorted(contributions[unit_id], key=lambda item: (item.resource_kind, item.source_key))
            )
            if isinstance(hit, BuildSearchHit):
                hits.append(
                    replace(
                        hit,
                        grouped=True,
                        records=records.get(hit.source_id, ()),
                        matches=sources,
                        title_match=highlight_title(hit.title, self.query),
                    )
                )
            elif isinstance(hit, MetadataSearchHit):
                hits.append(replace(hit, matches=sources, title_match=highlight_title(hit.title, self.query)))
        return tuple(hits)

    def _units(self, predicate: ColumnElement[bool]) -> CTE:
        """Pair each matching document with the unit it contributes to.

        A unit is identified by the ID of the document it is displayed as. A build
        can therefore appear in several rows: once for itself and once for each
        matching record it holds.
        """
        document = SearchDocument
        own = select(
            document.id.label("unit_id"),
            document.id.label("document_id"),
            document.normalized_title.label("unit_title"),
        ).where(predicate)
        if not self.request.group_by_build:
            return own.cte("matches")
        build = aliased(SearchDocument)
        holder = SearchDocumentFacet
        statuses = sorted(status.casefold() for status in self.statuses)
        # In record scope the result is headed by its best record, so it sorts by record title.
        record_title = (
            document.normalized_title if self.request.scope is SearchScope.RECORDS else build.normalized_title
        )
        held = (
            select(
                build.id.label("unit_id"),
                document.id.label("document_id"),
                record_title.label("unit_title"),
            )
            .select_from(document)
            .join(
                holder,
                (holder.document_id == document.id) & (holder.field_name == "holder"),
            )
            .join(
                build,
                (build.resource_kind == "build") & (build.source_key == holder.text_value),
            )
            .where(predicate, document.resource_kind == "record", func.lower(build.status).in_(statuses))
        )
        return union_all(own.where(document.resource_kind != "record"), held).cte("matches")

    async def _records(
        self,
        documents: Mapping[int, SearchDocument],
        predicate: ColumnElement[bool],
        ranked: Mapping[int, RankedCandidate],
    ) -> dict[str, tuple[RecordSearchHit, ...]]:
        build_keys = [document.source_key for document in documents.values() if document.resource_kind == "build"]
        build_titles = {
            document.source_key: document.title for document in documents.values() if document.resource_kind == "build"
        }
        if not build_keys:
            return {}
        holder = SearchDocumentFacet
        statement = (
            select(SearchDocument, holder.text_value, predicate.label("matched"))
            .join(
                holder,
                (holder.document_id == SearchDocument.id) & (holder.field_name == "holder"),
            )
            .where(SearchDocument.resource_kind == "record", holder.text_value.in_(build_keys))
        )
        if self.request.scope is SearchScope.RECORDS:
            statement = statement.where(predicate)
        rows = (await self.session.execute(statement)).all()
        record_ids = {row[0].id for row in rows}
        visible_holders: dict[int, list[int]] = defaultdict(list)
        ties = (
            await self.session.execute(
                select(holder.document_id, SearchDocument.source_key)
                .join(
                    SearchDocument,
                    (SearchDocument.resource_kind == "build") & (SearchDocument.source_key == holder.text_value),
                )
                .where(
                    holder.field_name == "holder",
                    holder.document_id.in_(record_ids),
                    func.lower(SearchDocument.status).in_(sorted(status.casefold() for status in self.statuses)),
                )
                .order_by(holder.document_id, holder.ordinal)
            )
        ).all()
        for document_id, source_key in ties:
            visible_holders[document_id].append(int(source_key))
        records: dict[str, list[RecordSearchHit]] = defaultdict(list)
        for document, build_key, matched in rows:
            assert build_key is not None  # The query restricted this facet to the selected build keys.
            candidate = ranked.get(document.id)
            hit = document_hit(document, candidate.score if candidate is not None else None)
            assert isinstance(hit, RecordSearchHit)
            metrics = document.document_data.get("holder_metrics", {})
            holder_metrics = metrics.get(build_key, hit.metrics) if isinstance(metrics, dict) else hit.metrics
            records[build_key].append(
                replace(
                    hit,
                    build_id=int(build_key),
                    build_title=build_titles[build_key],
                    holder_build_ids=tuple(visible_holders[document.id]),
                    metrics=cast(dict[str, str | int | float | bool], holder_metrics),
                    matched=bool(matched) and self.request.scope is not SearchScope.BUILDS,
                    title_match=highlight_title(hit.title, self.query) if matched else SearchText(hit.title),
                )
            )
        return {
            key: tuple(
                sorted(
                    values,
                    key=lambda hit: (
                        not hit.matched,
                        -(hit.score or 0),
                        int(hit.source_id.removeprefix("result:")),
                    ),
                )
            )
            for key, values in records.items()
        }

    @staticmethod
    def _match(document: SearchDocument, candidate: RankedCandidate | None) -> SearchMatch:
        kind, source_id = public_identity(document)
        return SearchMatch(kind, source_id, tuple(branch.value for branch in candidate.branches) if candidate else ())
