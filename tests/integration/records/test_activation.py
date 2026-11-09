"""Publish computed records through the real schema and transaction boundary."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from squid.builds.domain import BuildCategory
from squid.builds.history import BuildChange
from squid.events.models import DomainEvent
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.records.application.models import (
    CandidateFacet,
    CategoryCandidates,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    HolderHistoryEntry,
)
from squid.records.domain import (
    BaseTitle,
    BuildKind,
    Candidate,
    FormattedTitle,
    ResolutionStatus,
    TitleResolution,
    VersionScope,
)
from squid.records.domain.models import FacetKind
from squid.records.models import (
    ComputationRun,
    DefinitionFacet,
    MaterializationSource,
    RulesRevision,
    TitleDefinition,
    TitleHolderHistory,
)
from squid.tags.domain.models import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagDefinition
from squid.versions.models import Version
from tests.support.database import record_queries
from tests.support.submission_targets import seed_account_and_version, submission_build
from tests.support.unit_of_work import transact


def _batch(rules_revision_id: int, build_ids: list[int]) -> ComputationBatch:
    started_at = datetime(2020, 1, 1, tzinfo=UTC)
    candidates = tuple(
        Candidate(
            build_id=build_id,
            completion_at=started_at + timedelta(days=index),
            fixed_volume=20 - index,
        )
        for index, build_id in enumerate(build_ids)
    )
    record_title = CategoryCandidates(
        identity=CategoryIdentity(BuildKind.DOOR, "door|2x3|t[20]|Door", ()),
        facets=(),
        category_text=FormattedTitle("2x3 Door"),
        candidates=candidates,
        candidate_version_ids=tuple((candidate.build_id, frozenset()) for candidate in candidates),
    )
    computed = ComputedRecord(
        base_title=BaseTitle.SMALLEST,
        scope=VersionScope.ALL_TIME,
        version_id=None,
        category=record_title,
        title=FormattedTitle("Smallest 2x3 Door"),
        resolution=TitleResolution(ResolutionStatus.RESOLVED, holder_ids=(build_ids[-1],)),
        history=tuple(
            HolderHistoryEntry(
                build_ids=(candidate.build_id,),
                held_from=candidate.completion_at,
                held_until=candidates[index + 1].completion_at if index + 1 < len(candidates) else None,
            )
            for index, candidate in enumerate(candidates)
            if candidate.completion_at is not None
        ),
    )
    return ComputationBatch(
        rules_revision_id=rules_revision_id, kind=BuildKind.DOOR, version_id=None, records=(computed,)
    )


async def test_activation_batches_history_and_replaces_the_previous_run(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    builds = partial(SqlAlchemyUnitOfWork, sessions)
    build_ids: list[int] = []
    for _ in range(5):
        build = submission_build(BuildCategory.DOOR, owner)
        await transact(
            builds,
            lambda uow, build=build: uow.builds.save(
                build, change=BuildChange(operation="edit", source="test", actor_system="test")
            ),
            commit=True,
        )
        assert build.id is not None
        build_ids.append(build.id)
    records = partial(SqlAlchemyUnitOfWork, sessions)
    batch = _batch(await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True), build_ids)

    first_run = await transact(records, lambda uow: uow.records.activate(batch), commit=True)
    with record_queries(sessions) as statements:
        current_run = await transact(records, lambda uow: uow.records.activate(batch), commit=True)

    history_inserts = [
        statement for statement in statements if statement.startswith("INSERT INTO title_holder_history")
    ]
    assert len(history_inserts) == 1, "History must be inserted together, not flushed once per predecessor."
    async with sessions() as session:
        runs = (await session.execute(select(ComputationRun).order_by(ComputationRun.id))).scalars().all()
        history = (
            (
                await session.execute(
                    select(TitleHolderHistory)
                    .where(TitleHolderHistory.run_id == current_run)
                    .order_by(TitleHolderHistory.held_from)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await session.execute(
                    select(DomainEvent).where(DomainEvent.event_type == "record_run.activated").order_by(DomainEvent.id)
                )
            )
            .scalars()
            .all()
        )

    assert [(run.id, run.is_active) for run in runs] == [(first_run, False), (current_run, True)]
    assert [row.build_id for row in history] == build_ids
    assert [row.predecessor_id for row in history] == [None, *(row.id for row in history[:-1])]
    assert [row.held_until for row in history] == [*(row.held_from for row in history[1:]), None]
    assert [(event.aggregate_id, event.payload["previous_run_id"], event.payload["baseline"]) for event in events] == [
        (first_run, None, True),
        (current_run, first_run, False),
    ]
    published = await transact(
        records,
        lambda uow: uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=10
        ),
    )
    assert len(published) == 1
    assert published[0].title == "Smallest 2x3 Door"
    assert published[0].holder_build_ids == (build_ids[-1],)
    assert isinstance(published[0].computed_at, datetime)
    assert published[0].computed_at.utcoffset() == timedelta(0)


async def test_bulk_definitions_keep_public_requests_and_stable_title_ids(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    # Stored facets reference real restriction tags; the batch below uses ids 1 and 2.
    async with sessions.begin() as session:
        for tag_id, name in ((1, "Flush"), (2, "Seamless")):
            session.add(
                TagDefinition(
                    id=tag_id,
                    stable_key=name.casefold(),
                    display_name=name,
                    authority=TagAuthority.OFFICIAL,
                    semantic_kind=TagSemanticKind.RESTRICTION,
                    restriction_type="miscellaneous",
                    value_type=TagValueType.NONE,
                    moderation_status=TagModerationStatus.APPROVED,
                )
            )
    build = submission_build(BuildCategory.DOOR, owner)
    await transact(
        partial(SqlAlchemyUnitOfWork, sessions),
        lambda uow: uow.builds.save(build, change=BuildChange(operation="edit", source="test", actor_system="test")),
        commit=True,
    )
    assert build.id is not None
    records = partial(SqlAlchemyUnitOfWork, sessions)
    seed = _batch(await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True), [build.id])
    template = seed.records[0]
    # Cross the SQLAlchemy bulk page boundary; every category expands into five base titlees.
    batch = replace(
        seed,
        records=tuple(
            replace(
                template,
                base_title=base_title,
                title=FormattedTitle(
                    base_title.value, subtitle="First completion" if base_title is BaseTitle.FIRST else None
                ),
                category=replace(
                    template.category,
                    identity=CategoryIdentity(BuildKind.DOOR, f"door|{size}x3|t[20]|Door", (1,)),
                    facets=(CandidateFacet(id=1, kind=FacetKind.RESTRICTION, name="Flush"),),
                ),
                history=(),
            )
            for size in range(1, 221)
            for base_title in BaseTitle
        ),
    )
    with record_queries(sessions) as first_queries:
        await transact(records, lambda uow: uow.records.activate(batch), commit=True)
    identity = batch.records[0].category.identity
    titles = {base_title: FormattedTitle(f"Requested {base_title.value}") for base_title in BaseTitle}
    await transact(
        records, lambda uow: uow.records.save_requested_category(batch.rules_revision_id, identity, titles), commit=True
    )
    assert identity in await transact(records, lambda uow: uow.records.list_requested_categories(BuildKind.DOOR))

    async with sessions() as session:
        original = {
            (row.base_title, row.category_key): (row.id, row.title_id)
            for row in (
                await session.scalars(select(TitleDefinition).options(selectinload(TitleDefinition.record_title)))
            ).all()
        }
    with record_queries(sessions) as repeated_queries:
        await transact(records, lambda uow: uow.records.activate(batch), commit=True)
    assert identity in await transact(records, lambda uow: uow.records.list_requested_categories(BuildKind.DOOR))

    requested = CategoryIdentity(BuildKind.DOOR, "door|999x3|t[20]|Door", (1, 2))

    async def request_category() -> None:
        await transact(
            records,
            lambda uow: uow.records.save_requested_category(batch.rules_revision_id, requested, titles),
            commit=True,
        )

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(request_category)
            tasks.start_soon(request_category)

    async with sessions() as session:
        definitions = (
            await session.scalars(select(TitleDefinition).options(selectinload(TitleDefinition.record_title)))
        ).all()
        facets = (await session.scalars(select(DefinitionFacet))).all()
    assert len(definitions) == 1105
    assert len(facets) == 1110
    for definition in definitions:
        key = (definition.base_title, definition.category_key)
        if key in original:
            assert (definition.id, definition.title_id) == original[key]
        if definition.category_key in {identity.key, requested.key}:
            assert definition.materialization_source is MaterializationSource.PUBLIC_LOOKUP
    for queries in (first_queries, repeated_queries):
        record_title_writes = [query for query in queries if query.startswith("INSERT INTO record_titles")]
        definition_writes = [query for query in queries if query.startswith("INSERT INTO title_definitions")]
        assert len(record_title_writes) <= 2
        assert len(definition_writes) <= 2

    async with sessions.begin() as session:
        next_rules_revision = RulesRevision(
            document_hash="next-test-rules_revision", calculator_version="next", formatter_version="same"
        )
        session.add(next_rules_revision)
        await session.flush()
        next_rules_revision_id = next_rules_revision.id
        version_id = await session.scalar(select(Version.id).where(Version.data_version == 3953))
    assert version_id is not None
    next_batch = replace(batch, rules_revision_id=next_rules_revision_id, records=batch.records[:5])
    await transact(records, lambda uow: uow.records.activate(next_batch), commit=True)
    await transact(
        records,
        lambda uow: uow.records.activate(
            replace(
                next_batch,
                version_id=version_id,
                records=tuple(
                    replace(record, scope=VersionScope.UP_TO_DATE, version_id=version_id)
                    for record in next_batch.records
                ),
            )
        ),
        commit=True,
    )
    async with sessions() as session:
        next_definitions = (
            await session.scalars(
                select(TitleDefinition)
                .options(selectinload(TitleDefinition.record_title))
                .where(TitleDefinition.rules_revision_id == next_rules_revision_id)
            )
        ).all()
    assert len(next_definitions) == 10
    for definition in next_definitions:
        previous_id, previous_public_id = original[(definition.base_title, definition.category_key)]
        assert definition.id != previous_id
        if definition.version_scope is VersionScope.ALL_TIME:
            assert definition.title_id == previous_public_id
        else:
            assert definition.title_id != previous_public_id
