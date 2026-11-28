import uuid
from unittest.mock import AsyncMock, Mock

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.builds.models import ExtenderBuild
from squid.core.pagination import PageSelector
from squid.core.records.application.measurements import fixed_metric_snapshot
from squid.core.records.application.models import CategoryIdentity, RecordListFilters, RecordStatus
from squid.core.records.domain import (
    BuildKind,
    Candidate,
    RecordCriterion,
    TimingVariant,
    VersionScope,
)
from squid.core.records.infrastructure.repository import (
    CALCULATOR_VERSION,
    FORMATTER_VERSION,
    PostgresRecordRepository,
    _gap_from_row,
    extender_candidate,
)
from squid.core.records.models import (
    MaterializationSource,
    Record,
    RecordDefinition,
    RecordResult,
)
from tests.support.schema import model_table


def test_fastest_holder_snapshot_includes_volume_and_timing() -> None:
    holder = Candidate(build_id=1, fixed_volume=80, timing_variants=(TimingVariant((10, None)),))

    assert fixed_metric_snapshot(RecordCriterion.FASTEST, holder) == {"volume": 80, "timing": [10, None]}


def test_requested_category_key_round_trips() -> None:
    category = CategoryIdentity(
        kind=BuildKind.EXTENDER,
        base_key="extender|upward|3|t[20]",
        restriction_ids=(2, 4, 9),
    )

    assert CategoryIdentity.from_key(category.key) == category


def test_requested_category_parser_ignores_malformed_legacy_key() -> None:
    assert CategoryIdentity.from_key("not-a-record-category") is None


def test_gap_rows_carry_the_definition_title() -> None:
    definition = RecordDefinition(
        record_id=uuid.uuid4(),
        rules_revision_id=1,
        title="Fastest 2x2 Door",
        subtitle="All-time",
        materialization_source=MaterializationSource.EAGER,
    )
    definition.record = Record(
        criterion=RecordCriterion.FASTEST,
        build_kind=BuildKind.DOOR,
        version_scope=VersionScope.ALL_TIME,
        category_key="door:door|2x2|t[20]|Door:r[]:p[]",
    )
    definition.id = 5
    result = RecordResult(
        record_id=definition.record_id,
        run_id=1,
        definition_id=5,
        fingerprint="",
        gap_reasons={"missing": [{"build_id": 4, "field": "closing"}]},
    )

    gap = _gap_from_row(definition, result)

    assert gap.definition_id == 5
    assert gap.title == "Fastest 2x2 Door"
    assert gap.subtitle == "All-time"
    assert gap.build_ids == (4,)
    assert gap.fields == ("closing",)


def test_title_definitions_persist_canonical_title_metadata() -> None:
    assert {"title", "subtitle", "title_diagnostics"} <= set(RecordDefinition.__table__.columns.keys())


def test_title_definitions_allow_every_base_title() -> None:
    table = model_table(Record)
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "record_titles_base_title_check"
    )

    assert all(criterion.value in str(constraint.sqltext) for criterion in RecordCriterion)


def test_rules_versions_are_pinned() -> None:
    assert FORMATTER_VERSION == "2"
    assert CALCULATOR_VERSION == "8"


async def test_list_filters_are_applied_to_both_rows_and_count() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = Mock(unique=Mock(return_value=Mock(all=Mock(return_value=[]))))
    session.scalar.return_value = 0
    repository = PostgresRecordRepository(lambda: session)
    filters = RecordListFilters(
        build_kind=BuildKind.DOOR,
        version_scope=VersionScope.UP_TO_DATE,
        status=RecordStatus.UNRESOLVED,
        tag_ids=(17,),
    )
    await repository.list_published_records(selector=PageSelector(offset=8), filters=filters, page_size=9)

    for statement in (session.execute.call_args.args[0], session.scalar.call_args.args[0]):
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "record_titles.version_scope = 'up_to_date'" in sql
        assert "record_titles.build_kind = 'door'" in sql
        assert "jsonb_array_length" in sql
        assert "title_definition_tags.tag_id = 17" in sql
        assert "(,[0-9]+)*" in sql


def test_an_extender_without_measured_variants_competes_on_its_own_timings() -> None:
    extender = ExtenderBuild(id=1, orientation="Upward", extension_length=3, extension_time=4, retraction_time=6)

    candidate = extender_candidate(extender, ())
    measured = extender_candidate(extender, (TimingVariant((1, 2, None, None)),))

    assert candidate is not None
    assert measured is not None
    assert candidate.candidate.timing_variants == (TimingVariant((6, 4, None, None)),)
    assert measured.candidate.timing_variants == (TimingVariant((1, 2, None, None)),)
