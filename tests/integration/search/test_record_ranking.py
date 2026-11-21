"""Record searches rank category headings and return each title's current holders."""

from dataclasses import replace
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.search.application import SearchService
from squid.core.search.domain import RecordSearchHit, SearchRequest, SearchScope
from squid.core.search.infrastructure.projection import (
    RecordHolderProjectionSource,
    RecordProjectionSource,
    SearchProjectionRepository,
    build_record_projection,
)


async def test_record_heading_and_holder_matches(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    source = RecordProjectionSource(
        result_id=1,
        definition_id=1,
        status="resolved",
        history_complete=True,
        gap_reasons={},
        base_title="fastest",
        build_kind="door",
        version_scope="all_time",
        category_key="door",
        title="Fastest 2x2 Door",
        subtitle=None,
    )
    alice = RecordHolderProjectionSource(42, "Seamless 2x2 Door", {}, ("Alice",))
    bob = RecordHolderProjectionSource(57, "Flush Door", {}, ("Bob",))
    async with migrated_session_factory.begin() as session:
        store = SearchProjectionRepository(session)
        await store.replace(build_record_projection(replace(source, result_id=2, subtitle="Observerless"), (alice,)))
        await store.replace(build_record_projection(source, (alice, bob)))

    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    for query, expected in (
        ("fastest 2x2 door", "result:1"),
        ("fastest observerless 2x2 door", "result:2"),
        ("fastest 2x2 Bob", "result:1"),
        ("creator:Bob", "result:1"),
    ):
        for scope in (SearchScope.ALL, SearchScope.RECORDS):
            result = await service.search(SearchRequest(query, scope=scope))
            assert result.hits[0].source_id == expected
    page = await service.search(SearchRequest("seamless Alice"))
    assert page.total == 2
    general = next(hit for hit in page.hits if hit.source_id == "result:1")
    assert isinstance(general, RecordSearchHit)
    assert [(holder.build_id, holder.creators) for holder in general.holders] == [(42, ("Alice",)), (57, ("Bob",))]
