"""Export complete catalogue listings using the same readers as interactive browsing."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.actors import ActingContext
from squid.core.builds.application.queries import (
    BuildListSort,
    BuildQueryService,
    PublicBuildSummary,
    SemanticBuildSearch,
)
from squid.core.builds.domain import BuildListFilters, BuildStatus
from squid.core.errors import ValidationError
from squid.core.exports import Download, ExportFormat, ExportTable, encode_export
from squid.core.pagination import FIRST_PAGE
from squid.core.permissions.application import PermissionService
from squid.core.persistence.export import export_snapshot
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.models import RecordListFilters, RecordListSort
from squid.core.records.application.services import PublicRecordQueryService
from squid.core.search.application.services import SearchService
from squid.core.search.domain import SearchRequest, SearchScope

BUILD_COLUMNS = {
    "id": "id",
    "title": "string",
    "category": "string",
    "status": "string",
    "width": "integer?",
    "height": "integer?",
    "depth": "integer?",
    "version_spec": "string?",
}


def build_row(build: PublicBuildSummary) -> dict[str, object]:
    return {
        "id": str(build.id),
        "title": build.title,
        "category": build.category,
        "status": build.status,
        "width": build.dimensions.width,
        "height": build.dimensions.height,
        "depth": build.dimensions.depth,
        "version_spec": build.version_spec,
    }


class CatalogueExports:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], semantic: SemanticBuildSearch, permissions: PermissionService
    ):
        self.sessions = sessions
        self.semantic = semantic
        self.permissions = permissions

    @asynccontextmanager
    async def _readers(self) -> AsyncIterator[tuple[BuildQueryService, PublicRecordQueryService, SearchService]]:
        async with export_snapshot(self.sessions) as snapshot:
            # Each reader owns its units of work; bind them to savepoints inside the same snapshot.
            sessions = async_sessionmaker(await snapshot.connection(), join_transaction_mode="create_savepoint")
            factory = partial(SqlAlchemyUnitOfWork, sessions)
            builds = BuildQueryService(self.semantic, self.permissions, uow_factory=factory)
            yield (
                builds,
                PublicRecordQueryService(builds, uow_factory=factory),
                SearchService(builds=builds, uow_factory=factory),
            )

    async def builds(
        self,
        file_format: ExportFormat,
        *,
        statuses: frozenset[BuildStatus],
        submitter_account_id: int | None,
        filters: BuildListFilters,
        sort: BuildListSort,
        context: ActingContext,
    ) -> Download:
        async with self._readers() as (builds, _, _search):

            async def rows() -> AsyncIterator[dict[str, object]]:
                selector = FIRST_PAGE
                while True:
                    page = await builds.list_page(
                        statuses=statuses,
                        submitter_account_id=submitter_account_id,
                        filters=filters,
                        sort=sort,
                        context=context,
                        selector=selector,
                        page_size=100,
                    )
                    for build in page.items:
                        yield build_row(PublicBuildSummary.from_build(build))
                    if page.next is None:
                        break
                    selector = page.next.selector()

            return await encode_export([ExportTable("builds", BUILD_COLUMNS, rows())], file_format)

    async def records(
        self, file_format: ExportFormat, *, filters: RecordListFilters, sort: RecordListSort, context: ActingContext
    ) -> Download:
        async with self._readers() as (_, records, _search):

            async def rows() -> AsyncIterator[dict[str, object]]:
                selector = FIRST_PAGE
                while True:
                    page = await records.list_page(
                        filters=filters, sort=sort, context=context, selector=selector, page_size=100
                    )
                    for record in page.items:
                        yield {
                            "id": str(record.record_id),
                            "title": record.title,
                            "subtitle": record.subtitle,
                            "criterion": record.criterion,
                            "category": record.category_key,
                            "version_scope": record.version_scope,
                            "holder_build_ids": [str(holder.build_id) for holder in record.holders],
                            "metrics": {
                                name: {"value": metric.value, "unit": metric.unit}
                                for name, metric in record.metrics.items()
                            },
                        }
                    if page.next is None:
                        break
                    selector = page.next.selector()

            columns = {
                "id": "id",
                "title": "string",
                "subtitle": "string?",
                "criterion": "string",
                "category": "string",
                "version_scope": "string",
                "holder_build_ids": "json",
                "metrics": "json",
            }
            return await encode_export([ExportTable("records", columns, rows())], file_format)

    async def search(self, file_format: ExportFormat, request: SearchRequest, *, context: ActingContext) -> Download:
        build_only = request.scope == SearchScope.BUILDS and not request.group_by_build
        async with self._readers() as (builds, _records, search):

            async def rows() -> AsyncIterator[dict[str, object]]:
                offset = 0
                while True:
                    page = await search.search(replace(request, offset=offset, page_size=50), context=context)
                    ids = [int(hit.source_id) for hit in page.hits if hit.resource_kind == "build"]
                    summaries = (
                        {build.id: build for build in await builds.get_public_summaries(ids, context=context)}
                        if ids
                        else {}
                    )
                    for hit in page.hits:
                        details: dict[str, object]
                        if hit.resource_kind == "build":
                            build = summaries.get(int(hit.source_id))
                            if build is None:
                                continue
                            details = build_row(build)
                            if build_only:
                                yield details
                                continue
                            details["matching_records"] = [record.source_id for record in hit.records]
                            title = build.title
                        elif hit.resource_kind == "record":
                            title = hit.one_line_title
                            details = {
                                "criterion": hit.criterion,
                                "version_scope": hit.version_scope,
                                "holder_build_ids": [str(holder.build_id) for holder in hit.holders],
                                "retired": hit.retired,
                            }
                        else:
                            title = hit.title
                            details = {
                                "kind": hit.metadata_kind,
                                "description": hit.description,
                                "aliases": hit.aliases,
                            }
                        yield {"kind": hit.resource_kind, "id": hit.source_id, "title": title, "details": details}
                    offset += len(page.hits)
                    if offset >= page.total:
                        break
                    if not page.hits or page.next is None:
                        msg = "Search exceeds the exportable result window. Narrow the filters."
                        raise ValidationError(msg)

            table = (
                ExportTable("builds", BUILD_COLUMNS, rows())
                if build_only
                else ExportTable("search", {"kind": "string", "id": "id", "title": "string", "details": "json"}, rows())
            )
            return await encode_export([table], file_format)
