"""Turn the documents that match a search into one ordered page of results.

A result unit is what one row of a results page stands for. Without grouping,
every matching document is its own unit. With `SearchRequest.group_by_build`, a
matching record is replaced by the builds that hold it, so a build that holds
several matching records, or matches in its own right as well, appears once.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlalchemy import case, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from squid.core.pagination import MAX_PAGE_OFFSET
from squid.core.search.application.highlighting import highlight_title
from squid.core.search.application.ports import SearchSlice
from squid.core.search.application.ranking import RankedCandidate
from squid.core.search.domain import (
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
from squid.core.search.domain.text import TermExpansion
from squid.core.search.infrastructure.compiler import (
    compile_holder_query,
    compile_query,
    has_holder_filters,
    matching_holder,
)
from squid.core.search.infrastructure.hits import document_hit, public_identity
from squid.core.search.models import SearchDocument, SearchValue

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

    When grouping, indexed holder IDs connect records to build documents. Visibility
    applies to each holder, including ties, and a record without a visible holder
    produces no result. Record details are loaded separately for the page's
    builds, so a bounded relevance pool does not truncate a build's records.
    """

    def __init__(
        self, session: AsyncSession, request: SearchRequest, query: SearchQuery, expansion: TermExpansion
    ) -> None:
        self.session = session
        self.request = request
        self.query = query
        self.expansion = expansion
        self.holder_filters = has_holder_filters(query)
        self.statuses = request.visible_statuses if request.visible_statuses is not None else frozenset({"approved"})

    async def page(
        self, predicate: ColumnElement[bool], order: ResultOrder, *, warnings: tuple[str, ...] = ()
    ) -> SearchSlice:
        """Return one page of units, locating the anchor in the same order used for paging."""
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
        elif isinstance(order, FieldDefinition) and order.name == "title":
            statement = statement.add_columns(func.min(units.c.unit_title).label("order_value"))
        elif isinstance(order, FieldDefinition):
            statement = statement.outerjoin(
                SearchValue,
                (SearchValue.document_id == units.c.document_id) & (SearchValue.field_name == order.storage),
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
        offset = literal(self.request.offset)
        anchor = self.request.anchor
        if anchor is not None and total:
            positions = select(paged.c.unit_id, func.row_number().over(order_by=ordering).label("position")).cte()
            target = select(SearchDocument.id).where(
                SearchDocument.resource_kind == anchor.resource_kind, SearchDocument.source_key == anchor.source_id
            )
            if anchor.resource_kind == "record" and self.request.group_by_build:
                target = select(units.c.unit_id).where(units.c.document_id.in_(target))
            position = select(func.min(positions.c.position)).where(positions.c.unit_id.in_(target)).scalar_subquery()
            size = self.request.page_size
            aligned = (position - 1) // size * size
            offset = case((aligned <= MAX_PAGE_OFFSET, aligned), else_=offset)
        rows = (
            await self.session.execute(
                select(paged, offset.label("page_offset"))
                .order_by(*ordering)
                .offset(offset)
                .limit(self.request.page_size)
            )
        ).all()
        if not rows:
            return SearchSlice((), total, warnings)
        page_offset = rows[0].page_offset
        unit_ids = [row.unit_id for row in rows]
        documents = {
            document.id: document
            for document in (
                await self.session.scalars(select(SearchDocument).where(SearchDocument.id.in_(unit_ids)))
            ).all()
        }
        scores = {row.unit_id: float(row.order_value) if ranked is not None else None for row in rows}
        if not self.request.group_by_build:
            preferred_holders: dict[int, int | None] = {}
            if self.holder_filters:
                rows = await self.session.execute(
                    select(SearchDocument.id, matching_holder(self.query, self.expansion)).where(
                        SearchDocument.id.in_(unit_ids), SearchDocument.resource_kind == "record"
                    )
                )
                preferred_holders = {document_id: holder_id for document_id, holder_id in rows}
            hits: list[SearchHit] = []
            for unit_id in unit_ids:
                hit = document_hit(documents[unit_id], scores[unit_id], holder_id=preferred_holders.get(unit_id))
                if isinstance(hit, RecordSearchHit):
                    hit = replace(hit, title_match=highlight_title(hit.title, self.query))
                hits.append(hit)
            return SearchSlice(tuple(hits), total, warnings, page_offset)
        # Use every match rather than the ranked ones, so a hit also lists matches outside the candidate pool.
        grouped_hits = await self._grouped_hits(matches, unit_ids, documents, scores, ranked or {})
        return SearchSlice(grouped_hits, total, warnings, page_offset)

    @property
    def _descending(self) -> bool:
        return self.request.sort is not None and self.request.sort.direction is SortDirection.DESCENDING

    def _sort_value(self, field: FieldDefinition) -> ColumnElement[Any]:
        """Aggregate a unit's values for the sort field into the one it is ordered by.

        A unit can have several values, either as repeated field values in one document
        or across the records of a build. Ascending order uses the smallest and
        descending order the largest. Aggregating also keeps a unit from appearing
        once per value, which would shift page offsets.
        """
        value = {
            FieldType.TEXT: func.lower(SearchValue.text_value),
            FieldType.NUMBER: SearchValue.numeric_value,
            FieldType.TIMESTAMP: SearchValue.timestamp_value,
            FieldType.BOOLEAN: SearchValue.boolean_value,
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
        records = await self._records(documents, compile_query(self.query, self.expansion), ranked)
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
        holder = SearchValue
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
        if self.holder_filters:
            held = held.where(compile_holder_query(self.query, holder.holder_build_id, self.expansion))
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
        holder = SearchValue
        if self.holder_filters:
            predicate = compile_holder_query(self.query, holder.holder_build_id, self.expansion)
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
            assert build_key is not None  # The query restricted these rows to the selected builds.
            candidate = ranked.get(document.id)
            hit = document_hit(document, candidate.score if candidate is not None else None, holder_id=int(build_key))
            assert isinstance(hit, RecordSearchHit)
            records[build_key].append(
                replace(
                    hit,
                    build_id=int(build_key),
                    build_title=build_titles[build_key],
                    holder_build_ids=tuple(visible_holders[document.id]),
                    holders=tuple(holder for holder in hit.holders if holder.build_id in visible_holders[document.id]),
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
                        hit.source_id,
                    ),
                )
            )
            for key, values in records.items()
        }

    @staticmethod
    def _match(document: SearchDocument, candidate: RankedCandidate | None) -> SearchMatch:
        kind, source_id = public_identity(document)
        return SearchMatch(kind, source_id, tuple(branch.value for branch in candidate.branches) if candidate else ())
