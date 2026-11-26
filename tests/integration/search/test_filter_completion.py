"""Query-language value completion against live PostgreSQL.

Check case-insensitive prefix matching through the service and index selection with a
representative SQL query. Autocomplete runs on each keystroke, so selective prefixes should
avoid scanning the entire search_values table.
"""

from collections.abc import AsyncGenerator
from functools import partial

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.core.actors import ActingContext, AnonymousActor
from squid.core.persistence.base import Base
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.infrastructure.documents import FieldValue, SearchProjection
from squid.core.search.infrastructure.projection import SearchProjectionRepository
from squid.core.suggestions.application.queries import SuggestionQueryService
from squid.core.suggestions.domain import SuggestionRequest
from squid.core.suggestions.infrastructure.providers.search_query import SearchQueryProvider

pytestmark = pytest.mark.asyncio

_TABLES = [
    Base.metadata.tables[name]
    for name in ("search_documents", "search_values", "search_terms", "search_embedding_queue")
]

RESTRICTIONS = ["Seamless", "Semi-Seamless", "Full Lamp", "Flush", "Skydoor"]


class StaticFields:
    async def fields(self, *, context: ActingContext) -> FieldRegistry:
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


async def seed(session_factory: async_sessionmaker[AsyncSession], *, creator_count: int = 7) -> None:
    """Seed restrictions and enough creator values to check filtering, deduplication and limits."""
    async with session_factory() as session, session.begin():
        store = SearchProjectionRepository(session)
        for index, name in enumerate(RESTRICTIONS):
            await store.replace(
                SearchProjection("build", str(index + 1), f"{name} door", values=(FieldValue("restriction", name),))
            )
        # The first two documents share a creator, so DISTINCT has observable work to do.
        for index in range(creator_count):
            await store.replace(
                SearchProjection(
                    "build",
                    f"filler-{index}",
                    f"filler {index}",
                    values=(FieldValue("creator", f"Builder {max(0, index - 1)}"),),
                )
            )


@pytest.mark.usefixtures("search_tables")
async def test_filter_values_prefix_match_case_insensitively(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

    assert await repository.filter_values("restriction", "sea", limit=10) == ["Seamless"]
    assert await repository.filter_values("restriction", "SEA", limit=10) == ["Seamless"]
    assert await repository.filter_values("restriction", "f", limit=10) == ["Flush", "Full Lamp"]


@pytest.mark.usefixtures("search_tables")
async def test_filter_values_are_scoped_to_one_field(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    assert await repository.filter_values("restriction", "Builder", limit=10) == []


@pytest.mark.usefixtures("search_tables")
async def test_filter_values_are_distinct_and_bounded(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(async_session_factory)
    repository = SuggestionQueryService(uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))
    values = await repository.filter_values("creator", "builder", limit=5)
    assert values == [f"Builder {index}" for index in range(5)]


@pytest.mark.usefixtures("search_tables")
async def test_the_prefix_query_uses_the_prefix_index(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A selective prefix should use the index without disabling sequential scans."""
    await seed(async_session_factory, creator_count=2_000)
    async with async_session_factory() as session:
        await session.execute(text("ANALYZE search_values"))
        plan = "\n".join(
            row
            for row in (
                await session.scalars(
                    text(
                        "EXPLAIN SELECT DISTINCT text_value FROM search_values "
                        "WHERE field_name = 'creator' AND text_value IS NOT NULL "
                        "AND lower(text_value) LIKE 'builder 1%' "
                        "ORDER BY text_value LIMIT 10"
                    )
                )
            ).all()
        )
    assert "search_values_text_prefix_idx" in plan, plan
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

    with ActingContext.direct(AnonymousActor()).scope():
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

    with ActingContext.direct(AnonymousActor()).scope():
        result = await provider.suggest(SuggestionRequest(source="search_query", query="restriction:full", limit=10))

    assert [item.value for item in result.items] == ['"Full Lamp"']
    assert [item.label for item in result.items] == ["Full Lamp"]
