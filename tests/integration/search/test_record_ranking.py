"""Record searches rank category headings and return each title's current holders."""

from dataclasses import replace
from decimal import Decimal
from functools import partial
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.actors import ActingContext, AnonymousActor
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.search.application import SearchService
from squid.core.search.domain import BuildSearchHit, FieldValue, RecordSearchHit, SearchRequest, SearchScope
from squid.core.search.infrastructure.documents import SearchProjection
from squid.core.search.infrastructure.projection import (
    RecordHolderProjectionSource,
    RecordProjectionSource,
    SearchProjectionRepository,
    build_record_projection,
)

GENERAL = UUID(int=1)
RESTRICTED = UUID(int=2)


async def _builds(store: SearchProjectionRepository, *holders: RecordHolderProjectionSource) -> None:
    """Index the holders' builds, which record searches require to be approved."""
    for holder in holders:
        await store.replace(
            SearchProjection(
                "build",
                str(holder.build_id),
                holder.title,
                status="approved",
                values=(FieldValue("title", holder.title),),
            )
        )


async def test_record_heading_and_holder_matches(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    source = RecordProjectionSource(
        record_id=GENERAL,
        result_id=1,
        definition_id=1,
        history_complete=True,
        gap_reasons={},
        criterion="fastest",
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
        await _builds(store, alice, bob)
        await store.replace(
            build_record_projection(
                replace(source, record_id=RESTRICTED, result_id=2, subtitle="Observerless"), (alice,)
            )
        )
        await store.replace(build_record_projection(source, (alice, bob)))

    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    for query, expected in (
        ("fastest 2x2 door", f"record:{GENERAL}"),
        ("fastest observerless 2x2 door", f"record:{RESTRICTED}"),
        ("fastest 2x2 Bob", f"record:{GENERAL}"),
        ("creator:Bob", f"record:{GENERAL}"),
    ):
        for scope in (SearchScope.ALL, SearchScope.RECORDS):
            result = await service.search(
                SearchRequest(query, scope=scope), context=ActingContext.direct(AnonymousActor())
            )
            assert result.hits[0].source_id == expected
    page = await service.search(SearchRequest("seamless Alice"), context=ActingContext.direct(AnonymousActor()))
    assert page.total == 2
    general = next(hit for hit in page.hits if hit.source_id == f"record:{GENERAL}")
    assert isinstance(general, RecordSearchHit)
    assert [(holder.build_id, holder.creators) for holder in general.holders] == [(42, ("Alice",)), (57, ("Bob",))]


async def test_record_measurements_match_one_known_holder(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    source = RecordProjectionSource(
        record_id=GENERAL,
        result_id=1,
        definition_id=1,
        history_complete=True,
        gap_reasons={},
        criterion="fastest",
        build_kind="door",
        version_scope="all_time",
        category_key="door",
        title="Fastest",
        subtitle=None,
    )
    large = RecordHolderProjectionSource(42, "Large", {"volume": 120, "timing": [10, 8]})
    compact = RecordHolderProjectionSource(57, "Compact", {"volume": 80, "timing": [10, 15]})
    unknown = RecordHolderProjectionSource(60, "Unknown", {"volume": None, "timing": [10, 0]})
    async with migrated_session_factory.begin() as session:
        store = SearchProjectionRepository(session)
        await _builds(store, large, compact, unknown)
        await store.replace(build_record_projection(source, (large, compact, unknown)))

    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    for scope in (SearchScope.RECORDS, SearchScope.ALL):
        query = "criterion:fastest volume<=100 opening_time<=0.5s"
        (hit,) = (
            await service.search(SearchRequest(query, scope=scope), context=ActingContext.direct(AnonymousActor()))
        ).hits
        assert isinstance(hit, RecordSearchHit)
        assert hit.build_id == compact.build_id
        assert hit.metrics == {"volume": 80, "opening_time": 10, "visible_opening_time": 15}
        assert hit.matched_holder_id == compact.build_id
        assert hit.holder_build_ids == (42, 57, 60)

        (grouped,) = (
            await service.search(
                SearchRequest(query, scope=scope, group_by_build=True), context=ActingContext.direct(AnonymousActor())
            )
        ).hits
        assert isinstance(grouped, BuildSearchHit)
        assert grouped.source_id == str(compact.build_id)
        assert grouped.records[0].matched
        assert grouped.records[0].metrics == hit.metrics

        for impossible in (
            "volume<=100 visible_opening_time<=10gt",
            "volume<=100 AND NOT visible_opening_time>=10gt",
            "volume IS NOT SET visible_opening_time>0",
        ):
            assert (
                await service.search(
                    SearchRequest(impossible, scope=scope), context=ActingContext.direct(AnonymousActor())
                )
            ).total == 0

    outside = await service.search(
        SearchRequest("NOT volume<=100", scope=SearchScope.RECORDS),
        context=ActingContext.direct(AnonymousActor()),
    )
    assert {result.source_id for result in outside.hits} == {f"record:{GENERAL}"}
    only = await service.search(
        SearchRequest("NOT volume:[1 TO 200]", scope=SearchScope.RECORDS),
        context=ActingContext.direct(AnonymousActor()),
    )
    assert only.total == 0

    for query, holder_id in (
        ("volume IS SET", large.build_id),
        ("volume IS NOT SET visible_opening_time:0", unknown.build_id),
        ("NOT (volume IS SET) visible_opening_time IS SET", unknown.build_id),
    ):
        (hit,) = (
            await service.search(
                SearchRequest(query, scope=SearchScope.RECORDS), context=ActingContext.direct(AnonymousActor())
            )
        ).hits
        assert isinstance(hit, RecordSearchHit)
        assert hit.build_id == holder_id


async def test_build_presence_matches_indexed_values(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        store = SearchProjectionRepository(session)
        await store.replace(
            SearchProjection(
                "build",
                "1",
                "Full",
                description="A description",
                status="approved",
                values=(
                    FieldValue("title", "Full"),
                    FieldValue("description", "A description"),
                    FieldValue("volume", Decimal(100)),
                    FieldValue("opening_time", Decimal(0)),
                ),
            )
        )
        await store.replace(
            SearchProjection("build", "2", "Partial", status="approved", values=(FieldValue("title", "Partial"),))
        )

    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    for query, expected in (
        ("volume IS SET opening_time IS SET", "1"),
        ("volume IS NOT SET description IS NOT SET", "2"),
        ("title IS SET description IS SET", "1"),
    ):
        (hit,) = (
            await service.search(
                SearchRequest(query, scope=SearchScope.BUILDS), context=ActingContext.direct(AnonymousActor())
            )
        ).hits
        assert hit.source_id == expected
