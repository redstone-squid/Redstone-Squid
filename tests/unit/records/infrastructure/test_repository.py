from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import CheckConstraint, Index, Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.records.application.models import (
    CategoryCompetition,
    CategoryIdentity,
    ComputationBatch,
    ComputedRecord,
    HolderHistoryEntry,
)
from squid.records.domain import (
    BuildKind,
    CategoryText,
    RecordCandidate,
    RecordClass,
    RecordResolution,
    ResolutionStatus,
    VersionScope,
)
from squid.records.infrastructure.models import (
    RecordComputationRun,
    RecordDefinition,
    RecordHolderHistory,
    RecordResult,
)
from squid.records.infrastructure.repository import (
    CALCULATOR_VERSION,
    FORMATTER_VERSION,
    PostgresRecordRepository,
    parse_category_key,
)


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.assigned: set[int] = set()
        self.flushes = 0
        self.execute = AsyncMock()
        self.scalar = AsyncMock(return_value=None)

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass

    def begin(self) -> "FakeSession":
        return self

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushes += 1
        for value in self.added:
            identity = id(value)
            if (
                isinstance(value, (RecordComputationRun, RecordResult, RecordHolderHistory))
                and identity not in self.assigned
            ):
                value.id = len(self.assigned) + 1
                self.assigned.add(identity)


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self) -> FakeSession:
        return self.session


def test_requested_category_key_round_trips() -> None:
    category = CategoryIdentity(
        kind=BuildKind.EXTENDER,
        base_key="extender|upward|3|t[20]",
        restriction_ids=(2, 4, 9),
    )

    assert parse_category_key(category.key) == category


def test_requested_category_parser_ignores_malformed_legacy_key() -> None:
    assert parse_category_key("not-a-record-category") is None


def test_record_definitions_persist_canonical_title_metadata() -> None:
    assert {"title", "subtitle", "title_diagnostics"} <= set(RecordDefinition.__table__.columns.keys())


def test_record_definitions_allow_every_record_class() -> None:
    table = cast(Table, RecordDefinition.__table__)
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "record_definitions_record_class_check"
    )

    assert all(record_class.value in str(constraint.sqltext) for record_class in RecordClass)


def test_active_run_identity_is_global_across_rulesets() -> None:
    active_index = next(
        index
        for index in RecordComputationRun.__table_args__
        if isinstance(index, Index) and index.name == "record_computation_runs_one_active_idx"
    )

    assert tuple(column.name for column in active_index.columns) == ("build_kind", "version_id")
    assert FORMATTER_VERSION == "2"
    assert CALCULATOR_VERSION == "3"


@pytest.mark.asyncio
async def test_activate_flushes_holder_history_as_one_batch() -> None:
    started_at = datetime(2020, 1, 1, tzinfo=UTC)
    candidates = tuple(
        RecordCandidate(
            build_id=build_id,
            completion_at=started_at + timedelta(days=build_id),
            fixed_volume=20 - build_id,
        )
        for build_id in range(1, 6)
    )
    competition = CategoryCompetition(
        identity=CategoryIdentity(BuildKind.DOOR, "door|2x2|t[20]|Door", ()),
        facets=(),
        category_text=CategoryText("2x2 Door"),
        candidates=candidates,
        candidate_version_ids=tuple((candidate.build_id, frozenset()) for candidate in candidates),
    )
    computed = ComputedRecord(
        record_class=RecordClass.SMALLEST,
        scope=VersionScope.ALL_TIME,
        version_id=None,
        competition=competition,
        title=CategoryText("Smallest 2x2 Door"),
        resolution=RecordResolution(ResolutionStatus.RESOLVED, holder_ids=(5,)),
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
    batch = ComputationBatch(ruleset_id=7, kind=BuildKind.DOOR, version_id=None, records=(computed,))
    definition = RecordDefinition(
        ruleset_id=7,
        record_class=RecordClass.SMALLEST.value,
        build_kind=BuildKind.DOOR.value,
        version_scope=VersionScope.ALL_TIME.value,
        version_id=None,
        category_key=competition.identity.key,
        title=computed.title.title,
        subtitle=None,
        title_diagnostics=[],
        materialization_source="eager",
    )
    definition.id = 10
    session = FakeSession()
    repository = PostgresRecordRepository(cast(async_sessionmaker[AsyncSession], FakeSessionFactory(session)))

    with patch.object(
        PostgresRecordRepository,
        "_ensure_definitions",
        AsyncMock(return_value=(definition,)),
    ):
        run_id = await repository.activate(batch)

    histories = [value for value in session.added if isinstance(value, RecordHolderHistory)]
    assert run_id is not None
    assert session.flushes == 4
    assert len(histories) == 5
    assert histories[0].predecessor_id is None
    assert [history.predecessor_id for history in histories[1:]] == [history.id for history in histories[:-1]]
