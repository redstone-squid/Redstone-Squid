"""Offset pagination against live PostgreSQL.

Documents can have several values for a sort field. The SQL join must still count and page
each document once.
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import squid.search.infrastructure.repository as search_repository
from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.search.application import DEFAULT_FIELD_REGISTRY, SearchQueryParser
from squid.search.domain import BuildSearchHit, SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection
from squid.search.models import SearchDocument, SearchDocumentFacet
from squid.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

pytestmark = pytest.mark.asyncio

_TABLES = [Base.metadata.tables["search_documents"], Base.metadata.tables["search_document_facets"]]


@pytest.fixture
async def search_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def seed_builds(
    session_factory: async_sessionmaker[AsyncSession],
    titles: list[str],
    *,
    widths: dict[str, list[int]] | None = None,
) -> None:
    """Index one build document per title, with optional `width` facet values."""
    async with session_factory.begin() as session:
        for index, title in enumerate(titles):
            document = SearchDocument(
                resource_kind="build",
                source_key=str(index + 1),
                title=title,
                normalized_title=title.casefold(),
                fuzzy_text=title.casefold(),
                status="confirmed",
                source_hash=f"hash-{index}",
            )
            session.add(document)
            await session.flush()
            for ordinal, width in enumerate((widths or {}).get(title, [])):
                session.add(
                    SearchDocumentFacet(
                        document_id=document.id,
                        field_name="width",
                        ordinal=ordinal,
                        numeric_value=Decimal(width),
                    )
                )


async def seed_grouped_records(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await seed_builds(session_factory, ["Alpha door", "Bravo door", "Charlie door", "Pending door"])
    async with session_factory.begin() as session:
        await session.execute(text("UPDATE search_documents SET status = 'pending' WHERE source_key = '4'"))
        for result_id, title, holders, width in (
            (101, "Smallest door", [1, 2, 4], 2),
            (102, "Fastest door", [1], 9),
            (103, "Pending holder door", [4], 1),
            (104, "Holderless door", [], 1),
            (105, "Narrowest door", [2], 1),
        ):
            document = SearchDocument(
                resource_kind="record",
                source_key=f"result:{result_id}",
                title=title,
                normalized_title=title.casefold(),
                fuzzy_text=title.casefold(),
                source_hash=f"record-{result_id}",
                document_data={
                    "holder_build_ids": holders,
                    "base_title": title.split()[0].casefold(),
                    "version_scope": "all_time",
                    "metric": {"volume": 24},
                    "holder_metrics": {str(holder): {"volume": 24, "width": width} for holder in holders},
                },
            )
            session.add(document)
            await session.flush()
            session.add(
                SearchDocumentFacet(
                    document_id=document.id, field_name="width", ordinal=0, numeric_value=Decimal(width)
                )
            )
            session.add(
                SearchDocumentFacet(
                    document_id=document.id,
                    field_name="base_title",
                    ordinal=0,
                    text_value=title.split()[0].casefold(),
                )
            )
            for ordinal, holder in enumerate(holders):
                session.add(
                    SearchDocumentFacet(
                        document_id=document.id,
                        field_name="holder",
                        ordinal=ordinal,
                        text_value=str(holder),
                    )
                )
        session.add(
            SearchDocument(
                resource_kind="pattern",
                source_key="tag:1",
                title="Door pattern",
                normalized_title="door pattern",
                fuzzy_text="door pattern",
                source_hash="pattern",
            )
        )


@pytest.mark.parametrize("query", ["", "door"])
@pytest.mark.parametrize(
    ("scope", "expected"), [(SearchScope.RECORDS, 2), (SearchScope.BUILDS, 3), (SearchScope.ALL, 4)]
)
async def test_grouping_precedes_pagination_and_keeps_all_visible_holders(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    query: str,
    scope: SearchScope,
    expected: int,
) -> None:
    await seed_grouped_records(async_session_factory)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    hits = []
    for offset in range(expected + 1):
        page = await search(backend, SearchRequest(query, scope=scope, group_by_build=True, page_size=1, offset=offset))
        assert page.total == expected
        hits.extend(page.hits)
    assert len(hits) == expected
    assert len({(hit.resource_kind, hit.source_id) for hit in hits}) == expected
    builds = {hit.source_id: hit for hit in hits if isinstance(hit, BuildSearchHit)}
    assert "4" not in builds
    assert {record.source_id for record in builds["1"].records} == {"result:101", "result:102"}
    assert builds["2"].records[0].holder_build_ids == (1, 2)
    assert builds["2"].records[0].metrics == {"volume": 24, "width": 2}
    assert builds["1"].grouped
    if scope is SearchScope.ALL:
        assert {(source.resource_kind, source.source_id) for source in builds["1"].matches} == {
            ("build", "1"),
            ("record", "result:101"),
            ("record", "result:102"),
        }
    if scope is SearchScope.BUILDS:
        assert all(not record.matched for record in builds["1"].records)
    if query:
        assert any(source.branches for hit in hits if isinstance(hit, BuildSearchHit) for source in hit.matches)


@pytest.mark.parametrize(("direction", "first"), [(SortDirection.ASCENDING, "2"), (SortDirection.DESCENDING, "1")])
async def test_grouped_sort_aggregates_record_values_before_paging(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    direction: SortDirection,
    first: str,
) -> None:
    await seed_grouped_records(async_session_factory)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    page = await search(
        backend,
        SearchRequest(
            "",
            scope=SearchScope.RECORDS,
            group_by_build=True,
            page_size=1,
            sort=SearchSort("width", direction),
        ),
    )
    assert page.total == 2
    assert page.hits[0].source_id == first
    assert isinstance(page.hits[0], BuildSearchHit)
    assert len(page.hits[0].records) == 2


async def test_record_scope_includes_only_matching_records(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_grouped_records(async_session_factory)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    page = await search(backend, SearchRequest("base_title:smallest", group_by_build=True))
    assert page.total == 2
    for hit in page.hits:
        assert isinstance(hit, BuildSearchHit)
        assert [record.source_id for record in hit.records] == ["result:101"]


async def test_grouped_rank_limit_does_not_truncate_record_details(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_grouped_records(async_session_factory)
    monkeypatch.setattr(search_repository, "_CANDIDATE_LIMIT", 1)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    page = await search(backend, SearchRequest("door", scope=SearchScope.ALL, group_by_build=True))
    assert page.total == 1
    hit = page.hits[0]
    assert isinstance(hit, BuildSearchHit)
    assert hit.source_id == "1"
    assert {record.source_id for record in hit.records} == {"result:101", "result:102"}
    assert len(hit.matches) == 3
    empty = await search(backend, SearchRequest("xyzzyq", group_by_build=True))
    assert empty.total == 0
    assert empty.hits == ()


async def test_grouped_visibility_applies_to_each_holder_and_supports_an_empty_set(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_grouped_records(async_session_factory)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    page = await search(backend, SearchRequest("", group_by_build=True, visible_statuses=frozenset({"pending"})))
    assert page.total == 1
    hit = page.hits[0]
    assert isinstance(hit, BuildSearchHit)
    assert hit.source_id == "4"
    assert all(record.holder_build_ids == (4,) for record in hit.records)
    empty = await search(backend, SearchRequest("", group_by_build=True, visible_statuses=frozenset()))
    assert empty.total == 0
    assert empty.hits == ()


def request(query: str, *, offset: int = 0, page_size: int = 2, sort: SearchSort | None = None) -> SearchRequest:
    return SearchRequest(
        query=query,
        scope=SearchScope.BUILDS,
        mode=SearchMode.LEXICAL,
        page_size=page_size,
        offset=offset,
        sort=sort,
        visible_statuses=frozenset({"confirmed"}),
    )


async def search(
    backend: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    search_request: SearchRequest,
):
    return await transact(
        backend,
        lambda uow: uow.search.search(
            search_request,
            SearchQueryParser().parse(search_request.query),
            sort_field=DEFAULT_FIELD_REGISTRY.resolve(search_request.sort.field) if search_request.sort else None,
        ),
    )


async def test_filter_only_offsets_partition_the_matches_without_overlap(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_builds(async_session_factory, ["Alpha door", "Bravo door", "Charlie door", "Delta door"])
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)

    first = await search(backend, request("status:confirmed"))
    second = await search(backend, request("status:confirmed", offset=2))
    past_the_end = await search(backend, request("status:confirmed", offset=4))

    assert [hit.title for hit in first.hits] == ["Alpha door", "Bravo door"]
    assert [hit.title for hit in second.hits] == ["Charlie door", "Delta door"]
    assert past_the_end.hits == ()
    assert first.total == second.total == past_the_end.total == 4


async def test_a_document_with_several_sort_values_is_paged_once(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Aggregate repeated width facets before paging to avoid shifting later offsets."""
    await seed_builds(
        async_session_factory,
        ["Alpha door", "Bravo door", "Charlie door"],
        widths={"Alpha door": [2, 9], "Bravo door": [4], "Charlie door": [6]},
    )
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    sort = SearchSort("width", SortDirection.ASCENDING)

    first = await search(backend, request("status:confirmed", sort=sort))
    second = await search(backend, request("status:confirmed", offset=2, sort=sort))

    assert [hit.title for hit in first.hits] == ["Alpha door", "Bravo door"]
    assert [hit.title for hit in second.hits] == ["Charlie door"]
    assert first.total == 3


async def test_sort_direction_picks_the_extreme_value_it_orders_by(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ascending anchors on a document's smallest value, descending on its largest."""
    await seed_builds(
        async_session_factory,
        ["Alpha door", "Bravo door"],
        widths={"Alpha door": [1, 9], "Bravo door": [5]},
    )
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)

    ascending = await search(backend, request("status:confirmed", sort=SearchSort("width", SortDirection.ASCENDING)))
    descending = await search(backend, request("status:confirmed", sort=SearchSort("width", SortDirection.DESCENDING)))

    assert [hit.title for hit in ascending.hits] == ["Alpha door", "Bravo door"]
    assert [hit.title for hit in descending.hits] == ["Alpha door", "Bravo door"]


async def test_documents_without_the_sort_field_come_last(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_builds(
        async_session_factory,
        ["Alpha door", "Bravo door", "Charlie door"],
        widths={"Bravo door": [4], "Charlie door": [2]},
    )
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    sort = SearchSort("width", SortDirection.ASCENDING)

    page = await search(backend, request("status:confirmed", page_size=3, sort=sort))

    assert [hit.title for hit in page.hits] == ["Charlie door", "Bravo door", "Alpha door"]
    assert page.total == 3


async def test_ranked_offsets_are_stable_across_requests(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_builds(async_session_factory, ["Piston door", "Piston trapdoor", "Piston hipster door"])
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)

    whole = await search(backend, request("piston", page_size=3))
    first = await search(backend, request("piston"))
    second = await search(backend, request("piston", offset=2))

    assert [hit.title for hit in whole.hits] == [
        *(hit.title for hit in first.hits),
        *(hit.title for hit in second.hits),
    ]
    assert first.total == whole.total == 3


async def test_public_title_suggestions_share_search_visibility(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    documents = (
        ("build", "confirmed", "Piston confirmed"),
        ("build", "CONFIRMED", "Piston uppercase"),
        ("build", "pending", "Piston pending"),
        ("build", "denied", "Piston denied"),
        ("build", None, "Piston unclassified"),
        ("record", None, "Piston record"),
        ("tag", None, "Piston metadata"),
    )
    async with async_session_factory.begin() as session:
        session.add_all(
            SearchDocument(
                resource_kind=kind,
                source_key=str(index),
                title=title,
                normalized_title=title.casefold(),
                fuzzy_text=title.casefold(),
                status=status,
                source_hash=f"visibility-{index}",
            )
            for index, (kind, status, title) in enumerate(documents)
        )
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    query = SearchQueryParser().parse("piston")
    public_titles = {"Piston confirmed", "Piston uppercase", "Piston record", "Piston metadata"}

    assert set(await transact(backend, lambda uow: uow.search.suggest(query, limit=25))) == public_titles
    public = await search(backend, SearchRequest(query="piston", scope=SearchScope.ALL, page_size=25))
    assert {hit.title for hit in public.hits} == public_titles
    staff = await search(
        backend,
        SearchRequest(query="piston", scope=SearchScope.BUILDS, visible_statuses=frozenset({"pending"})),
    )
    assert [hit.title for hit in staff.hits] == ["Piston pending"]
