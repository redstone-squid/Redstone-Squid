"""Offset pagination against live PostgreSQL.

The paging arithmetic is only as good as the SQL underneath it, and the failure this file exists
for -- a document with several values for the sort field being counted more than once -- is
invisible against an in-memory fake, because the duplication happens in the join.
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.search.application import SearchQueryParser
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection
from squid.search.infrastructure.models import SearchDocument, SearchDocumentFacet
from squid.search.infrastructure.repository import PostgresSearchBackend

pytestmark = pytest.mark.asyncio

_TABLES = [Base.metadata.tables["search_documents"], Base.metadata.tables["search_document_facets"]]


@pytest.fixture
async def search_tables(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
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
    backend: PostgresSearchBackend,
    search_request: SearchRequest,
):
    return await backend.search(
        search_request, SearchQueryParser().parse(search_request.query), offset=search_request.offset
    )


async def test_filter_only_offsets_partition_the_matches_without_overlap(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_builds(async_session_factory, ["Alpha door", "Bravo door", "Charlie door", "Delta door"])
    backend = PostgresSearchBackend(async_session_factory)

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
    """The bug the aggregated join exists to prevent.

    Facets are unique per `(document_id, field_name, ordinal)`, so a build recorded at two widths
    has two `width` rows. Joining the facet table directly would return that document once per row
    and shift every later OFFSET by the number of duplicates that fell before it.
    """
    await seed_builds(
        async_session_factory,
        ["Alpha door", "Bravo door", "Charlie door"],
        widths={"Alpha door": [2, 9], "Bravo door": [4], "Charlie door": [6]},
    )
    backend = PostgresSearchBackend(async_session_factory)
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
    backend = PostgresSearchBackend(async_session_factory)

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
    backend = PostgresSearchBackend(async_session_factory)
    sort = SearchSort("width", SortDirection.ASCENDING)

    page = await search(backend, request("status:confirmed", page_size=3, sort=sort))

    assert [hit.title for hit in page.hits] == ["Charlie door", "Bravo door", "Alpha door"]
    assert page.total == 3


async def test_ranked_offsets_are_stable_across_requests(
    search_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed_builds(async_session_factory, ["Piston door", "Piston trapdoor", "Piston hipster door"])
    backend = PostgresSearchBackend(async_session_factory)

    whole = await search(backend, request("piston", page_size=3))
    first = await search(backend, request("piston"))
    second = await search(backend, request("piston", offset=2))

    assert [hit.title for hit in whole.hits] == [
        *(hit.title for hit in first.hits),
        *(hit.title for hit in second.hits),
    ]
    assert first.total == whole.total == 3
