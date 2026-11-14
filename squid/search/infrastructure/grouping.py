"""Group indexed record holders and direct build matches before paging."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from sqlalchemy import BigInteger, case, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from squid.search.application.fields import FieldDefinition, FieldType
from squid.search.application.highlighting import highlight_title
from squid.search.application.ports import SearchSlice
from squid.search.application.ranking import RankedCandidate
from squid.search.domain import (
    BuildSearchHit,
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
from squid.search.infrastructure.hits import document_hit, public_identity
from squid.search.models import SearchDocument, SearchDocumentFacet


class GroupedSearch:
    """Page build identities in SQL, then enrich only the selected groups.

    Holder facets connect records to build documents. Visibility applies to each
    holder, including ties, and holderless records do not produce build results.
    Relevance remains bounded by the backend's candidate pool; record details are
    loaded separately so that pool does not truncate a selected build's records.
    """

    def __init__(self, session: AsyncSession, request: SearchRequest, query: SearchQuery) -> None:
        self.session = session
        self.request = request
        self.query = query
        self.statuses = request.visible_statuses if request.visible_statuses is not None else frozenset({"confirmed"})

    async def search(
        self,
        predicate: ColumnElement[bool],
        query_predicate: ColumnElement[bool],
        *,
        offset: int,
        field: FieldDefinition | None = None,
        ranked: Mapping[int, RankedCandidate] | None = None,
        warnings: tuple[str, ...] = (),
    ) -> SearchSlice:
        if ranked is not None and not ranked:
            return SearchSlice((), 0, warnings)
        mapping = self._mapping(predicate)
        selected = mapping
        if ranked is not None:
            selected = select(mapping).where(mapping.c.document_id.in_(ranked)).cte("ranked_groups")
        columns = [
            selected.c.group_id,
            func.min(selected.c.group_title).label("title"),
            func.min(selected.c.kind).label("kind"),
            func.min(selected.c.source_key).label("source_key"),
        ]
        statement = select(*columns).group_by(selected.c.group_id)
        if ranked is not None:
            score = case({key: value.score for key, value in ranked.items()}, value=selected.c.document_id, else_=0.0)
            statement = statement.add_columns(func.max(score).label("score"))
        elif field is not None:
            ascending = self.request.sort is None or self.request.sort.direction is SortDirection.ASCENDING
            value = {
                FieldType.TEXT: func.lower(SearchDocumentFacet.text_value),
                FieldType.NUMBER: SearchDocumentFacet.numeric_value,
                FieldType.TIMESTAMP: SearchDocumentFacet.timestamp_value,
                FieldType.BOOLEAN: SearchDocumentFacet.boolean_value,
            }[field.value_type]
            if field.value_type is FieldType.BOOLEAN:
                aggregate = func.bool_and(value) if ascending else func.bool_or(value)
            else:
                aggregate = func.min(value) if ascending else func.max(value)
            statement = statement.outerjoin(
                SearchDocumentFacet,
                (SearchDocumentFacet.document_id == selected.c.document_id)
                & (SearchDocumentFacet.field_name == (field.storage_name or field.name)),
            ).add_columns(aggregate.label("sort_value"))
        groups = statement.cte("search_groups")
        total = await self.session.scalar(select(func.count()).select_from(groups)) or 0
        build_id = case((groups.c.kind == "build", groups.c.source_key.cast(BigInteger)), else_=None)
        if ranked is not None:
            order = [groups.c.score.desc(), groups.c.kind, build_id, groups.c.source_key]
        else:
            order = [groups.c.title, groups.c.kind, build_id, groups.c.source_key]
            if field is not None:
                ascending = self.request.sort is None or self.request.sort.direction is SortDirection.ASCENDING
                value = groups.c.sort_value
                order = [value.is_(None), value.asc() if ascending else value.desc(), *order]
        rows = (
            await self.session.execute(select(groups).order_by(*order).offset(offset).limit(self.request.page_size))
        ).all()
        if not rows:
            return SearchSlice((), total, warnings)
        group_ids = [row.group_id for row in rows]
        documents = {
            document.id: document
            for document in (
                await self.session.scalars(select(SearchDocument).where(SearchDocument.id.in_(group_ids)))
            ).all()
        }
        contributions: dict[int, list[SearchDocument]] = defaultdict(list)
        matches = (
            await self.session.execute(
                select(mapping.c.group_id, SearchDocument)
                .join(SearchDocument, SearchDocument.id == mapping.c.document_id)
                .where(mapping.c.group_id.in_(group_ids))
                .distinct()
            )
        ).all()
        for group_id, document in matches:
            contributions[group_id].append(document)
        records = await self._records(documents, query_predicate, ranked or {})
        hits: list[SearchHit] = []
        for row in rows:
            document = documents[row.group_id]
            score = float(row.score) if ranked is not None else None
            hit = document_hit(document, score)
            sources = tuple(
                self._match(source, (ranked or {}).get(source.id))
                for source in sorted(
                    contributions[row.group_id], key=lambda item: (item.resource_kind, item.source_key)
                )
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
        return SearchSlice(tuple(hits), total, warnings)

    def _mapping(self, predicate: ColumnElement[bool]) -> CTE:
        document = SearchDocument
        build = aliased(SearchDocument)
        holder = SearchDocumentFacet
        statuses = sorted(status.casefold() for status in self.statuses)
        direct = select(
            document.id.label("group_id"),
            document.id.label("document_id"),
            document.normalized_title.label("group_title"),
            document.resource_kind.label("kind"),
            document.source_key.label("source_key"),
        ).where(
            predicate,
            document.resource_kind != "record",
            (document.resource_kind != "build") | func.lower(document.status).in_(statuses),
        )
        record_title = (
            document.normalized_title if self.request.scope is SearchScope.RECORDS else build.normalized_title
        )
        held = (
            select(
                build.id.label("group_id"),
                document.id.label("document_id"),
                record_title.label("group_title"),
                literal("build").label("kind"),
                build.source_key.label("source_key"),
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
        return union_all(direct, held).cte("build_matches")

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
