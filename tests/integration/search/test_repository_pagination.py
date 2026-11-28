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

import squid.core.search.infrastructure.repository as search_repository
from squid.core.persistence.base import Base
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.search.application import DEFAULT_FIELD_REGISTRY, SearchQueryParser
from squid.core.search.application.results import BuildSearchHit
from squid.core.search.domain import (
    FieldValue,
    SearchMode,
    SearchRequest,
    SearchResultAnchor,
    SearchScope,
    SearchSort,
    SortDirection,
)
from squid.core.search.infrastructure.documents import SearchProjection
from squid.core.search.infrastructure.projection import SearchProjectionRepository
from squid.core.unit_of_work import UnitOfWorkFactory
from tests.support.unit_of_work import transact

pytestmark = pytest.mark.asyncio

_TABLES = [
    Base.metadata.tables[name]
    for name in ("search_documents", "search_values", "search_terms", "search_embedding_queue")
]


@pytest.fixture
async def search_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def _index(
    session: AsyncSession,
    kind: str,
    key: str,
    title: str,
    *,
    status: str | None = None,
    values: tuple[FieldValue, ...] = (),
    data: dict[str, object] | None = None,
) -> None:
    await SearchProjectionRepository(session).replace(
        SearchProjection(
            kind, key, title, status=status, document_data=data or {}, values=(FieldValue("title", title), *values)
        )
    )


async def seed_builds(
    session_factory: async_sessionmaker[AsyncSession],
    titles: list[str],
    *,
    widths: dict[str, list[int]] | None = None,
) -> None:
    """Index one build document per title, with optional `width` values."""
    async with session_factory.begin() as session:
        for index, title in enumerate(titles):
            widths_of = (widths or {}).get(title, [])
            await _index(
                session,
                "build",
                str(index + 1),
                title,
                status="approved",
                values=(
                    FieldValue("status", "approved"),
                    *(FieldValue("width", Decimal(width)) for width in widths_of),
                ),
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
            await _index(
                session,
                "record",
                f"record:{result_id}",
                title,
                status="current",
                data={
                    "holder_build_ids": holders,
                    "base_title": title.split()[0].casefold(),
                    "version_scope": "all_time",
                    "metric": {"volume": 24},
                    "holder_metrics": {str(holder): {"volume": 20 + width} for holder in holders},
                },
                values=(
                    FieldValue("width", Decimal(width)),
                    FieldValue("base_title", title.split()[0].casefold()),
                    *(FieldValue("holder", str(holder), holder) for holder in holders),
                ),
            )
        await _index(session, "pattern", "tag:1", "Door pattern")


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
    assert {record.source_id for record in builds["1"].records} == {"record:101", "record:102"}
    assert builds["2"].records[0].holder_build_ids == (1, 2)
    assert builds["2"].records[0].metrics["volume"].value == 22
    if scope is SearchScope.ALL:
        assert {(source.resource_kind, source.source_id) for source in builds["1"].matches} == {
            ("build", "1"),
            ("record", "record:101"),
            ("record", "record:102"),
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
    page = await search(backend, SearchRequest("criterion:smallest", group_by_build=True))
    assert page.total == 2
    for hit in page.hits:
        assert isinstance(hit, BuildSearchHit)
        assert [record.source_id for record in hit.records] == ["record:101"]


@pytest.mark.parametrize(
    ("anchor", "offset", "build"),
    [
        (SearchResultAnchor("build", "1"), 1, "1"),
        (SearchResultAnchor("record", "record:102"), 1, "1"),
        (SearchResultAnchor("record", "record:101"), 0, "2"),
        (SearchResultAnchor("record", "record:103"), 0, "2"),
    ],
)
async def test_sorted_grouped_search_locates_the_anchor_in_visible_results(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    anchor: SearchResultAnchor,
    offset: int,
    build: str,
) -> None:
    await seed_grouped_records(async_session_factory)
    page = await search(
        partial(SqlAlchemyUnitOfWork, async_session_factory),
        SearchRequest("", page_size=1, sort=SearchSort("width"), group_by_build=True, anchor=anchor),
    )

    assert page.offset == offset
    assert page.hits[0].source_id == build


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
    assert {record.source_id for record in hit.records} == {"record:101", "record:102"}
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
        visible_statuses=frozenset({"approved"}),
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

    first = await search(backend, request("status:approved"))
    second = await search(backend, request("status:approved", offset=2))
    past_the_end = await search(backend, request("status:approved", offset=4))

    assert [hit.title for hit in first.hits] == ["Alpha door", "Bravo door"]
    assert [hit.title for hit in second.hits] == ["Charlie door", "Delta door"]
    assert past_the_end.hits == ()
    assert first.total == second.total == past_the_end.total == 4


async def test_a_document_with_several_sort_values_is_paged_once(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Aggregate repeated width filters before paging to avoid shifting later offsets."""
    await seed_builds(
        async_session_factory,
        ["Alpha door", "Bravo door", "Charlie door"],
        widths={"Alpha door": [2, 9], "Bravo door": [4], "Charlie door": [6]},
    )
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    sort = SearchSort("width", SortDirection.ASCENDING)

    first = await search(backend, request("status:approved", sort=sort))
    second = await search(backend, request("status:approved", offset=2, sort=sort))

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

    ascending = await search(backend, request("status:approved", sort=SearchSort("width", SortDirection.ASCENDING)))
    descending = await search(backend, request("status:approved", sort=SearchSort("width", SortDirection.DESCENDING)))

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

    page = await search(backend, request("status:approved", page_size=3, sort=sort))

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
        ("build", "approved", "Piston approved"),
        ("build", "APPROVED", "Piston uppercase"),
        ("build", "pending", "Piston pending"),
        ("build", "rejected", "Piston rejected"),
        ("build", None, "Piston unclassified"),
        ("record", "current", "Piston record"),
        ("tag", None, "Piston metadata"),
    )
    async with async_session_factory.begin() as session:
        for index, (kind, status, title) in enumerate(documents):
            await _index(session, kind, str(index), title, status=status)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    query = SearchQueryParser().parse("piston")
    public_titles = {"Piston approved", "Piston uppercase", "Piston record", "Piston metadata"}

    assert set(await transact(backend, lambda uow: uow.search.suggest(query, limit=25))) == public_titles
    public = await search(backend, SearchRequest(query="piston", scope=SearchScope.ALL, page_size=25))
    assert {hit.title for hit in public.hits} == public_titles
    staff = await search(
        backend,
        SearchRequest(query="piston", scope=SearchScope.BUILDS, visible_statuses=frozenset({"pending"})),
    )
    assert [hit.title for hit in staff.hits] == ["Piston pending"]


@pytest.mark.parametrize(
    ("scope", "expected"),
    [(SearchScope.BUILDS, 3), (SearchScope.RECORDS, 5), (SearchScope.METADATA, 1), (SearchScope.ALL, 9)],
)
async def test_count_includes_every_visible_match_in_the_scope(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
    scope: SearchScope,
    expected: int,
) -> None:
    await seed_grouped_records(async_session_factory)
    backend = partial(SqlAlchemyUnitOfWork, async_session_factory)
    request = SearchRequest("door", scope=scope)

    count = await transact(
        backend, lambda uow: uow.search.count(request, SearchQueryParser(scope=scope).parse(request.query))
    )

    assert count == expected


async def test_width_histogram_counts_each_matching_document_once_per_bin(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_builds(async_session_factory, ["A", "B", "C", "Unknown"], widths={"A": [1, 2], "B": [1], "C": [40]})
    request = SearchRequest("", scope=SearchScope.BUILDS, page_size=1)
    query = SearchQueryParser(DEFAULT_FIELD_REGISTRY, scope=request.scope).parse(request.query)
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        assert await uow.search.histogram(request, query, "width") == [(1, 3, 2), (39, 41, 1)]
