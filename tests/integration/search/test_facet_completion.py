"""Query-language value completion against live PostgreSQL.

Check case-insensitive prefix matching through the service and index selection with a
representative SQL query. Autocomplete runs on each keystroke, so selective prefixes should
avoid scanning the entire facet table.
"""

from collections.abc import AsyncGenerator
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.search.models import SearchDocument, SearchDocumentFacet
from squid.suggestions.application.queries import SuggestionQueryService
from squid.suggestions.domain import SuggestionRequest
from squid.suggestions.infrastructure.providers.search_query import SearchQueryProvider

pytestmark = pytest.mark.asyncio

_TABLES = [Base.metadata.tables["search_documents"], Base.metadata.tables["search_document_facets"]]

RESTRICTIONS = ["Seamless", "Semi-Seamless", "Full Lamp", "Flush", "Skydoor"]


class StaticFields:
    async def fields(self) -> FieldRegistry:
        return DEFAULT_FIELD_REGISTRY


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


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Index one document per restriction, plus filler so the planner has a reason to use an index."""
    async with session_factory() as session, session.begin():
        for index, name in enumerate(RESTRICTIONS):
            document = SearchDocument(
                resource_kind="build",
                source_key=str(index + 1),
                title=f"{name} door",
                normalized_title=f"{name.casefold()} door",
                fuzzy_text=f"{name.casefold()} door",
                source_hash=f"hash-{index}",
            )
            session.add(document)
            await session.flush()
            session.add(
                SearchDocumentFacet(document_id=document.id, field_name="restriction", ordinal=0, text_value=name)
            )
        # Give the planner enough unrelated rows to favor a selective prefix index scan.
        for index in range(2_000):
            filler = SearchDocument(
                resource_kind="build",
                source_key=f"filler-{index}",
                title=f"filler {index}",
                normalized_title=f"filler {index}",
                fuzzy_text=f"filler {index}",
                source_hash=f"filler-hash-{index}",
            )
            session.add(filler)
            await session.flush()
            session.add(
                SearchDocumentFacet(
                    document_id=filler.id, field_name="creator", ordinal=0, text_value=f"Builder {index}"
                )
            )
    async with session_factory() as session, session.begin():
        await session.execute(text("ANALYZE search_document_facets"))


@pytest.mark.usefixtures("search_tables")
async def test_facet_values_prefix_match_case_insensitively(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    assert await repository.facet_values("restriction", "sea", limit=10) == ["Seamless"]
    assert await repository.facet_values("restriction", "SEA", limit=10) == ["Seamless"]
    assert await repository.facet_values("restriction", "f", limit=10) == ["Flush", "Full Lamp"]


@pytest.mark.usefixtures("search_tables")
async def test_facet_values_are_scoped_to_one_field(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    assert await repository.facet_values("restriction", "Builder", limit=10) == []


@pytest.mark.usefixtures("search_tables")
async def test_facet_values_are_distinct_and_bounded(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    values = await repository.facet_values("creator", "builder", limit=5)
    assert len(values) == 5
    assert len(set(values)) == 5


@pytest.mark.usefixtures("search_tables")
async def test_the_prefix_query_uses_the_prefix_index(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A selective prefix should use the index without disabling sequential scans."""
    await seed(async_session_factory)
    async with async_session_factory() as session:
        plan = "\n".join(
            row
            for row in (
                await session.scalars(
                    text(
                        "EXPLAIN SELECT DISTINCT text_value FROM search_document_facets "
                        "WHERE field_name = 'creator' AND text_value IS NOT NULL "
                        "AND lower(text_value) LIKE 'builder 1%' "
                        "ORDER BY text_value LIMIT 10"
                    )
                )
            ).all()
        )
    assert "search_document_facets_text_prefix_idx" in plan, plan
    assert "Seq Scan" not in plan, plan


@pytest.mark.usefixtures("search_tables")
async def test_a_half_typed_field_expression_suggests_values_instead_of_raising(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Completion replaces only the value, preserving the field and colon."""
    await seed(async_session_factory)
    provider = SearchQueryProvider(
        StaticFields(),
        SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory)),
    )

    result = await provider.suggest(SuggestionRequest(source="search_query", query="restriction:sea", limit=10))

    assert [item.value for item in result.items] == ["Seamless"]
    assert result.replacement is not None
    assert (result.replacement.start, result.replacement.end) == (12, 15)


@pytest.mark.usefixtures("search_tables")
async def test_a_value_containing_a_space_is_quoted_so_it_parses_back_as_one_token(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    provider = SearchQueryProvider(
        StaticFields(),
        SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory)),
    )

    result = await provider.suggest(SuggestionRequest(source="search_query", query="restriction:full", limit=10))

    assert [item.value for item in result.items] == ['"Full Lamp"']
    assert [item.label for item in result.items] == ["Full Lamp"]
