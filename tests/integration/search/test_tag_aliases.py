"""Assigned tag names and aliases match through the real projection and search queries."""

from functools import partial

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import OtherBuild
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.domain import BuildKind, RecordCriterion, ResolutionStatus, VersionScope
from squid.core.records.models import (
    ComputationRun,
    ComputationStatus,
    MaterializationSource,
    Record,
    RecordDefinition,
    RecordResult,
    RulesRevision,
)
from squid.core.search.application import SearchService
from squid.core.search.domain import BuildSearchHit, SearchPage, SearchRequest, SearchScope, SearchSort
from squid.core.search.infrastructure.projection import SearchProjectionWorker
from squid.core.search.models import SearchDocument, SearchProjectionQueueItem
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagAlias, TagAssignment, TagDefinition
from tests.support.build_edits import build_edit_session


async def _refresh(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions.begin() as session:
        while True:
            succeeded, failed = await SearchProjectionWorker(session).process_batch(
                context=ActingContext.worker("test-worker")
            )
            assert failed == 0
            if succeeded == 0:
                break


@pytest.fixture
async def catalogue(migrated_session_factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    async with build_edit_session(migrated_session_factory) as session:
        account = Account()
        session.add(account)
        await session.flush()
        definitions = [
            TagDefinition(
                stable_key=f"search-{index}",
                display_name=name,
                semantic_kind=kind,
                authority=TagAuthority.OFFICIAL,
                restriction_type="component" if kind is TagSemanticKind.RESTRICTION else None,
                value_type=TagValueType.NONE,
                moderation_status=status,
                aliases=[TagAlias(alias=alias, normalized_alias=alias.casefold())],
            )
            for index, (name, alias, kind, status) in enumerate(
                (
                    ("No Observers", "Observerless", TagSemanticKind.RESTRICTION, TagModerationStatus.APPROVED),
                    ("Full Lacing", "Laced", TagSemanticKind.PATTERN, TagModerationStatus.APPROVED),
                    ("Compact", "Pocket Sized", TagSemanticKind.SHOWCASE, TagModerationStatus.APPROVED),
                    ("Unapproved Feature", "Ghostkeyword", TagSemanticKind.RESTRICTION, TagModerationStatus.PENDING),
                )
            )
        ]
        session.add_all(definitions)
        await session.flush()
        target = OtherBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            submission_time=Instant.now(),
            edited_time=Instant.now(),
            tags=[TagAssignment(definition) for definition in definitions],
        )
        # A description mentioning an alias must not satisfy an explicit restriction filter.
        textual = OtherBuild(
            submitter_account_id=account.id,
            submission_status=BuildStatus.APPROVED,
            submission_time=Instant.now(),
            edited_time=Instant.now(),
            description="observerless",
        )
        session.add_all([target, textual])
        await session.flush()
        identifiers = target.id, textual.id, definitions[0].id
    await _refresh(migrated_session_factory)
    return identifiers


def _ids(page: SearchPage) -> set[int]:
    return {int(hit.source_id) for hit in page.hits}


async def test_keyword_aliases_and_exact_filters_keep_display_names(
    migrated_session_factory: async_sessionmaker[AsyncSession], catalogue: tuple[int, int, int]
) -> None:
    target_id, textual_id, _ = catalogue
    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    page = await service.search(
        SearchRequest("observerless", scope=SearchScope.BUILDS, group_by_build=True),
        context=ActingContext.direct(Actor()),
    )
    assert _ids(page) == {target_id, textual_id}
    target = next(hit for hit in page.hits if hit.source_id == str(target_id))
    assert isinstance(target, BuildSearchHit)
    assert "no observers" in target.tags
    assert "observerless" not in target.tags
    assert "ghostkeyword" not in target.tags
    assert any("full_text" in match.branches for match in target.matches)

    for query in (
        "restriction:observerless",
        'restriction:"  NO   OBSERVERS  "',
        'restriction:"  OBSERVERLESS  "',
        'tag:"No Observers"',
        "tag:observerless",
        "pattern:laced",
        "type:LACED",
        'tag:"pocket   SIZED"',
        "restriction:observerless AND pattern:laced",
        "restriction:observerless OR pattern:missing",
    ):
        page = await service.search(
            SearchRequest(query, scope=SearchScope.BUILDS, group_by_build=True), context=ActingContext.direct(Actor())
        )
        assert _ids(page) == {target_id}, query
        assert page.total == 1

    for query in ('"pocket sized"', "laced"):
        page = await service.search(
            SearchRequest(query, scope=SearchScope.BUILDS), context=ActingContext.direct(Actor())
        )
        assert _ids(page) == {target_id}, query

    for query in ("NOT restriction:observerless", "NOT restriction:observerless AND NOT pattern:laced"):
        assert _ids(
            await service.search(SearchRequest(query, scope=SearchScope.BUILDS), context=ActingContext.direct(Actor()))
        ) == {textual_id}

    for query in ("ghostkeyword", "restriction:ghostkeyword", "tag:ghostkeyword", "restriction:observers"):
        assert (
            _ids(
                await service.search(
                    SearchRequest(query, scope=SearchScope.BUILDS), context=ActingContext.direct(Actor())
                )
            )
            == set()
        ), query

    sorted_page = await service.search(
        SearchRequest("restriction:observerless", scope=SearchScope.BUILDS, sort=SearchSort("created_at")),
        context=ActingContext.direct(Actor()),
    )
    assert _ids(sorted_page) == {target_id}
    metadata = await service.search(
        SearchRequest("restriction:observerless", scope=SearchScope.METADATA), context=ActingContext.direct(Actor())
    )
    assert [hit.title for hit in metadata.hits] == ["No Observers"]


async def test_alias_addition_rename_and_removal_refresh_assigned_builds(
    migrated_session_factory: async_sessionmaker[AsyncSession], catalogue: tuple[int, int, int]
) -> None:
    target_id, _, tag_id = catalogue
    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    async with migrated_session_factory.begin() as session:
        definition = await session.get(TagDefinition, tag_id)
        assert definition is not None
        session.add(
            TagAlias(
                alias="Detector Free",
                normalized_alias="detector free",
                definition=definition,
            )
        )
    for old, new in ((None, "Detector Free"), ("Detector Free", "Sensor Free"), ("Sensor Free", None)):
        if old is not None:
            async with migrated_session_factory.begin() as session:
                predicate = (TagAlias.tag_id == tag_id) & (TagAlias.alias == old)
                if new is None:
                    await session.execute(delete(TagAlias).where(predicate))
                else:
                    await session.execute(
                        update(TagAlias).where(predicate).values(alias=new, normalized_alias=new.casefold())
                    )
        async with migrated_session_factory() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SearchProjectionQueueItem)
                    .where(
                        SearchProjectionQueueItem.resource_kind == "build",
                        SearchProjectionQueueItem.source_key == str(target_id),
                    )
                )
                == 1
            )
        await _refresh(migrated_session_factory)
        if new is not None:
            for query in (f'"{new}"', f'restriction:"{new}"'):
                assert _ids(
                    await service.search(
                        SearchRequest(query, scope=SearchScope.BUILDS), context=ActingContext.direct(Actor())
                    )
                ) == {target_id}
        if old is not None:
            assert (
                _ids(
                    await service.search(
                        SearchRequest(f'restriction:"{old}"', scope=SearchScope.BUILDS),
                        context=ActingContext.direct(Actor()),
                    )
                )
                == set()
            )
            # Check the indexed text too: fuzzy matching could still find the renamed synonym.
            async with migrated_session_factory() as session:
                document = await session.scalar(
                    select(SearchDocument).where(
                        SearchDocument.resource_kind == "build", SearchDocument.source_key == str(target_id)
                    )
                )
                assert document is not None
                assert old.casefold() not in document.fuzzy_text


async def test_migration_requeues_existing_builds_and_tag_metadata(
    migrated_session_factory: async_sessionmaker[AsyncSession], catalogue: tuple[int, int, int]
) -> None:
    target_id, textual_id, tag_id = catalogue
    revision = ScriptDirectory.from_config(Config(toml_file="pyproject.toml")).get_revision("f9a3b5c1d746")
    assert revision is not None

    def requeue(connection: Connection) -> None:
        with Operations.context(MigrationContext.configure(connection)):
            revision.module.upgrade()

    async with migrated_session_factory.begin() as session:
        connection = await session.connection()
        await connection.run_sync(requeue)
        queued = set(
            (
                await session.execute(
                    select(SearchProjectionQueueItem.resource_kind, SearchProjectionQueueItem.source_key)
                )
            ).all()
        )
        assert {("build", str(target_id)), ("build", str(textual_id)), ("metadata", f"tag:{tag_id}")} <= queued
    await _refresh(migrated_session_factory)
    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    assert _ids(
        await service.search(
            SearchRequest("restriction:observerless", scope=SearchScope.BUILDS), context=ActingContext.direct(Actor())
        )
    ) == {target_id}


async def test_record_category_filters_match_without_holders_and_refresh_aliases(
    migrated_session_factory: async_sessionmaker[AsyncSession], catalogue: tuple[int, int, int]
) -> None:
    _, _, restriction_id = catalogue
    async with migrated_session_factory.begin() as session:
        pattern_id = await session.scalar(select(TagDefinition.id).where(TagDefinition.stable_key == "search-1"))
        assert pattern_id is not None
        rules = RulesRevision(document_hash="search-record-categories", calculator_version="1", formatter_version="1")
        session.add(rules)
        await session.flush()
        result_ids: list[str] = []
        for kind, categories in (
            (
                BuildKind.DOOR,
                (
                    f"door:door|2x3|t[{pattern_id}]|Trapdoor:r[{restriction_id}]:p[]",
                    "door:door|2x4x2|t[]|Door:r[]:p[]",
                ),
            ),
            (BuildKind.EXTENDER, ("extender:extender|Horizontal|4|t[Double]:r[]:p[]",)),
        ):
            run = ComputationRun(
                rules_revision_id=rules.id, build_kind=kind, status=ComputationStatus.COMPLETED, is_active=True
            )
            session.add(run)
            await session.flush()
            for key in categories:
                record = Record(
                    criterion=RecordCriterion.SMALLEST,
                    build_kind=kind,
                    version_scope=VersionScope.ALL_TIME,
                    category_key=key,
                )
                session.add(record)
                await session.flush()
                definition = RecordDefinition(
                    record_id=record.public_id,
                    rules_revision_id=rules.id,
                    title="Smallest",
                    materialization_source=MaterializationSource.EAGER,
                )
                session.add(definition)
                await session.flush()
                result = RecordResult(run_id=run.id, definition_id=definition.id, status=ResolutionStatus.NO_CANDIDATE)
                session.add(result)
                await session.flush()
                result_ids.append(f"result:{result.id}")
    await _refresh(migrated_session_factory)

    service = SearchService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))

    async def matches(query: str) -> set[str]:
        page = await service.search(
            SearchRequest(query, scope=SearchScope.RECORDS), context=ActingContext.direct(Actor())
        )
        return {hit.source_id for hit in page.hits}

    door, thick_door, extender = result_ids
    assert await matches("door_width:2 door_height:[3 TO 4]") == {door, thick_door}
    assert await matches("door_depth:1 orientation:trapdoor type:laced restriction:observerless") == {door}
    assert await matches("door_height>=4 OR restriction:observerless") == {door, thick_door}
    assert await matches("door_depth:2 type:regular") == {thick_door}
    assert await matches("orientation:horizontal extender_length>=4 type:double") == {extender}

    async with migrated_session_factory.begin() as session:
        await session.execute(
            update(TagAlias)
            .where(TagAlias.tag_id == restriction_id, TagAlias.alias == "Observerless")
            .values(alias="Detector Free", normalized_alias="detector free")
        )
    await _refresh(migrated_session_factory)
    assert await matches('restriction:"detector free"') == {door}
    assert await matches("restriction:observerless") == set()
