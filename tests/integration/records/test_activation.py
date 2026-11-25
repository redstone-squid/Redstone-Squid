"""Publish computed records through the real schema and transaction boundary."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.core.builds.domain import BuildCategory
from squid.core.events.models import DomainEvent
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.application.models import (
    CandidateTag,
    CategoryCandidates,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecordResult,
    HolderHistoryEntry,
    PriorPublication,
)
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    FormattedTitle,
    RecordCriterion,
    RecordResolution,
    VersionScope,
)
from squid.core.records.domain.models import RecordTagRole
from squid.core.records.models import (
    MaterializationSource,
    RecordDefinition,
    RecordDefinitionTag,
    RecordHolderHistory,
    RecordResult,
    RulesRevision,
)
from squid.core.tags.domain.models import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.versions.models import Version
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact
from tests.support.database import record_queries
from tests.support.submission_targets import seed_account_and_version, submission_build


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
        tags=(),
        category_text=FormattedTitle("2x3 Door"),
        candidates=candidates,
    )
    computed = ComputedRecordResult(
        criterion=RecordCriterion.SMALLEST,
        scope=VersionScope.ALL_TIME,
        version_id=None,
        category=record_title,
        title=FormattedTitle("Smallest 2x3 Door"),
        resolution=RecordResolution(holder_ids=(build_ids[-1],)),
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
        rules_revision_id=rules_revision_id,
        kind=BuildKind.DOOR,
        input_generation=1,
        categories=frozenset({(None, record_title.identity.key)}),
        records=(computed,),
    )


def _with_records(batch: ComputationBatch, records: tuple[ComputedRecordResult, ...]) -> ComputationBatch:
    return replace(
        batch,
        records=records,
        categories=batch.categories | {(record.version_id, record.category.identity.key) for record in records},
    )


def _changes(events: list[DomainEvent]) -> list[list[tuple[str, list[int], list[int]]]]:
    return [
        [(change["change"], change["previous_holder_ids"], change["holder_ids"]) for change in event.payload["changes"]]  # type: ignore[index,union-attr]
        for event in events
    ]


async def _events(sessions: async_sessionmaker[AsyncSession]) -> list[DomainEvent]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    select(DomainEvent).where(DomainEvent.event_type == "record_run.published").order_by(DomainEvent.id)
                )
            )
            .scalars()
            .all()
        )


async def _doors(sessions: async_sessionmaker[AsyncSession], count: int) -> list[int]:
    owner = await seed_account_and_version(sessions)
    builds = partial(SqlAlchemyUnitOfWork, sessions)
    build_ids: list[int] = []
    for _ in range(count):
        build = submission_build(BuildCategory.DOOR, owner)
        await transact(builds, lambda uow, build=build: uow.builds.save(build), commit=True)
        assert build.id is not None
        build_ids.append(build.id)
    return build_ids


async def test_publication_updates_results_in_place_and_reports_holder_changes(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_ids = await _doors(sessions, 5)
    records = partial(SqlAlchemyUnitOfWork, sessions)
    batch = _batch(await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True), build_ids)

    await transact(records, lambda uow: uow.records.publish(batch), commit=True)
    async with records() as uow:
        (original,) = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=10
        )
    # A changed title publishes even when the holders stay the same, but announces nothing.
    batch = _with_records(batch, (replace(batch.records[0], title=FormattedTitle("Updated smallest door")),))
    with record_queries(sessions) as statements:
        await transact(records, lambda uow: uow.records.publish(batch), commit=True)
    history_inserts = [
        statement for statement in statements if statement.startswith("INSERT INTO title_holder_history")
    ]
    assert len(history_inserts) == 1, "History must be inserted together, not flushed once per predecessor."
    # Handing the record to another build is a holder change.
    moved = replace(batch.records[0], resolution=RecordResolution(holder_ids=(build_ids[0],)))
    await transact(records, lambda uow: uow.records.publish(_with_records(batch, (moved,))), commit=True)

    async with sessions() as session:
        history = (
            (
                await session.execute(
                    select(RecordHolderHistory)
                    .where(RecordHolderHistory.record_id == original.record_id)
                    .order_by(RecordHolderHistory.held_from)
                )
            )
            .scalars()
            .all()
        )
    assert [row.build_id for row in history] == build_ids
    assert [row.predecessor_id for row in history] == [None, *(row.id for row in history[:-1])]
    assert [row.held_until for row in history] == [*(row.held_from for row in history[1:]), None]
    events = await _events(sessions)
    assert [event.payload["baseline"] for event in events] == [True, False]
    assert _changes(events) == [
        [("appeared", [], [build_ids[-1]])],
        [("holders_changed", [build_ids[-1]], [build_ids[0]])],
    ]
    async with records() as uow:
        latest = await uow.records.get_latest_record(original.record_id)
    assert latest is not None
    assert latest.result_id == original.result_id
    assert latest.title == "Updated smallest door"
    assert latest.holder_build_ids == (build_ids[0],)
    assert isinstance(latest.computed_at, datetime)
    assert latest.computed_at.utcoffset() == timedelta(0)


async def test_retirement_keeps_history_and_revival_keeps_the_record(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_ids = await _doors(sessions, 2)
    records = partial(SqlAlchemyUnitOfWork, sessions)
    batch = _batch(await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True), build_ids)
    await transact(records, lambda uow: uow.records.publish(batch), commit=True)
    async with records() as uow:
        (live,) = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=10
        )

    retired = await transact(
        records, lambda uow: uow.records.publish(replace(batch, records=(), input_generation=2)), commit=True
    )
    assert retired.retired == 1
    async with records() as uow:
        assert await uow.records.count_published_records() == 0
        shown = await uow.records.get_latest_record(live.record_id)
        history = await uow.records.get_record_history(live.record_id)
        prior = await uow.records.prior_publication(BuildKind.DOOR, frozenset(build_ids))
    assert shown is not None
    assert shown.result_id is None
    assert shown.retired_at is not None
    assert shown.holder_build_ids == ()
    assert [period.build_ids for period in history] == [(build_ids[0],), (build_ids[1],)]
    assert history[-1].held_until == shown.retired_at
    assert prior == PriorPublication()

    await transact(records, lambda uow: uow.records.publish(replace(batch, input_generation=3)), commit=True)
    async with records() as uow:
        revived = await uow.records.get_latest_record(live.record_id)
        prior = await uow.records.prior_publication(BuildKind.DOOR, frozenset(build_ids))
    assert revived is not None
    assert revived.retired_at is None
    assert revived.result_id is not None
    assert prior.memberships == {build_id: batch.categories for build_id in build_ids}
    assert [change[0][0] for change in _changes(await _events(sessions))] == ["appeared", "retired", "revived"]


async def test_an_older_generation_cannot_replace_a_newer_publication(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_ids = await _doors(sessions, 2)
    records = partial(SqlAlchemyUnitOfWork, sessions)
    batch = _batch(await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True), build_ids)
    await transact(records, lambda uow: uow.records.publish(replace(batch, input_generation=5)), commit=True)

    stale = replace(batch.records[0], resolution=RecordResolution(holder_ids=(build_ids[0],)))
    await transact(
        records,
        lambda uow: uow.records.publish(_with_records(replace(batch, input_generation=4), (stale,))),
        commit=True,
    )
    await transact(
        records, lambda uow: uow.records.publish(replace(batch, records=(), input_generation=4)), commit=True
    )

    async with records() as uow:
        (published,) = await uow.records.list_published_records(
            offset=0, after_id=None, before_id=None, descending=False, limit=10
        )
    assert published.holder_build_ids == (build_ids[-1],)


async def test_a_backstop_records_changes_no_pending_work_explains(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_ids = await _doors(sessions, 2)
    records = partial(SqlAlchemyUnitOfWork, sessions)
    batch = _batch(await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True), build_ids)
    await transact(records, lambda uow: uow.records.publish(batch), commit=True)

    # Expected changes are not drift; an unexplained one is.
    moved = replace(batch.records[0], resolution=RecordResolution(holder_ids=(build_ids[0],)))
    explained = await transact(
        records,
        lambda uow: uow.records.publish(_with_records(replace(batch, expected=batch.categories), (moved,))),
        commit=True,
    )
    unexplained = await transact(
        records,
        lambda uow: uow.records.publish(replace(batch, expected=frozenset(), full=True)),
        commit=True,
    )

    assert (explained.drift, unexplained.drift) == (0, 1)
    async with records() as uow:
        (drift,) = await uow.records.list_drift(offset=0, after_id=None, before_id=None, limit=10)
        assert await uow.records.count_drift() == 1
    assert drift.title == "Smallest 2x3 Door"
    assert drift.published["holder_build_ids"] == [build_ids[0]]
    assert drift.computed["holder_build_ids"] == [build_ids[-1]]


async def test_bulk_definitions_keep_public_requests_and_stable_title_ids(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    owner = await seed_account_and_version(sessions)
    # Stored tags reference real restriction tags; the batch below uses ids 1 and 2.
    async with build_edit_session(sessions) as session:
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
        lambda uow: uow.builds.save(build),
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
                criterion=criterion,
                title=FormattedTitle(
                    criterion.value, subtitle="First completion" if criterion is RecordCriterion.FIRST else None
                ),
                category=replace(
                    template.category,
                    identity=CategoryIdentity(BuildKind.DOOR, f"door|{size}x3|t[20]|Door", (1,)),
                    tags=(
                        CandidateTag(id=2, kind=RecordTagRole.PATTERN, name="Spiral"),
                        CandidateTag(id=2, kind=RecordTagRole.TYPE, name="Door"),
                        CandidateTag(id=2, kind=RecordTagRole.CATEGORY, name="Utility"),
                        CandidateTag(id=1, kind=RecordTagRole.RESTRICTION, name="Flush"),
                    ),
                ),
                history=(),
            )
            for size in range(1, 221)
            for criterion in RecordCriterion
        ),
    )
    batch = _with_records(batch, batch.records)
    with record_queries(sessions) as first_queries:
        await transact(records, lambda uow: uow.records.publish(batch), commit=True)
    identity = batch.records[0].category.identity
    titles = {criterion: FormattedTitle(f"Requested {criterion.value}") for criterion in RecordCriterion}
    await transact(
        records, lambda uow: uow.records.save_requested_category(batch.rules_revision_id, identity, titles), commit=True
    )
    assert identity in await transact(records, lambda uow: uow.records.list_requested_categories(BuildKind.DOOR))

    async with sessions() as session:
        original = {
            (row.criterion, row.category_key): (row.id, row.record_id)
            for row in (
                await session.scalars(select(RecordDefinition).options(selectinload(RecordDefinition.record)))
            ).all()
        }
    with record_queries(sessions) as repeated_queries:
        await transact(records, lambda uow: uow.records.publish(batch), commit=True)
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
            await session.scalars(select(RecordDefinition).options(selectinload(RecordDefinition.record)))
        ).all()
        tags = (await session.scalars(select(RecordDefinitionTag))).all()
    assert len(definitions) == 1105
    assert len(tags) == 1110
    assert all(tag.tag_role is RecordTagRole.RESTRICTION for tag in tags)
    assert all(tag.display_order == 0 for tag in tags if tag.tag_id == 1)
    for definition in definitions:
        key = (definition.criterion, definition.category_key)
        if key in original:
            assert (definition.id, definition.record_id) == original[key]
        if definition.category_key in {identity.key, requested.key}:
            assert definition.materialization_source is MaterializationSource.PUBLIC_LOOKUP
    for queries in (first_queries, repeated_queries):
        record_title_writes = [query for query in queries if query.startswith("INSERT INTO record_titles")]
        definition_writes = [query for query in queries if query.startswith("INSERT INTO title_definitions")]
        assert len(record_title_writes) <= 2
        assert len(definition_writes) <= 2

    async with build_edit_session(sessions) as session:
        # Publication accepts only the active revision.
        await session.execute(update(RulesRevision).values(activated_at=None))
        next_rules_revision = RulesRevision(
            document_hash="next-test-rules_revision",
            calculator_version="next",
            formatter_version="same",
            activated_at=Instant.now(),
        )
        session.add(next_rules_revision)
        await session.flush()
        next_rules_revision_id = next_rules_revision.id
        version_id = await session.scalar(select(Version.id).where(Version.data_version == 3953))
    assert version_id is not None
    next_batch = replace(
        batch, rules_revision_id=next_rules_revision_id, records=batch.records[:5], categories=frozenset()
    )
    next_batch = _with_records(next_batch, next_batch.records)
    await transact(records, lambda uow: uow.records.publish(next_batch), commit=True)
    current = tuple(
        replace(record, scope=VersionScope.UP_TO_DATE, version_id=version_id) for record in next_batch.records
    )
    await transact(
        records,
        lambda uow: uow.records.publish(_with_records(replace(next_batch, categories=frozenset()), current)),
        commit=True,
    )
    async with sessions() as session:
        next_definitions = (
            await session.scalars(
                select(RecordDefinition)
                .options(selectinload(RecordDefinition.record))
                .where(RecordDefinition.rules_revision_id == next_rules_revision_id)
            )
        ).all()
    assert len(next_definitions) == 10
    for definition in next_definitions:
        previous_id, previous_public_id = original[(definition.criterion, definition.category_key)]
        assert definition.id != previous_id
        if definition.version_scope is VersionScope.ALL_TIME:
            assert definition.record_id == previous_public_id
        else:
            assert definition.record_id != previous_public_id


async def test_unchanged_publication_preserves_ids_and_events(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    build_ids = await _doors(sessions, 1)
    records = partial(SqlAlchemyUnitOfWork, sessions)
    revision = await transact(records, lambda uow: uow.records.active_rules_revision_id(), commit=True)
    batch = _batch(revision, build_ids)
    await transact(records, lambda uow: uow.records.publish(batch), commit=True)
    async with sessions() as session:
        before = await session.scalar(select(RecordResult))
        assert before is not None
    with record_queries(sessions) as statements:
        summary = await transact(records, lambda uow: uow.records.publish(batch), commit=True)
    assert summary.changed == 0
    assert not any(
        statement.startswith(("INSERT INTO title_results", "UPDATE title_results SET run_id"))
        for statement in statements
    )
    async with sessions() as session:
        after = await session.scalar(select(RecordResult))
        assert after is not None
    assert (after.id, after.run_id, after.computed_at) == (before.id, before.run_id, before.computed_at)
    assert after.last_checked_at >= before.last_checked_at
    assert len(await _events(sessions)) == 1
