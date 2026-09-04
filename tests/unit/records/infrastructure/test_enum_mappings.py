import re
from enum import StrEnum

import pytest
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.engine import default

from squid.persistence.types import StrEnumText
from squid.records.domain import BuildKind, RecordClass, ResolutionStatus, VersionScope
from squid.records.infrastructure.models import (
    RecordComputationRun,
    RecordComputationStatus,
    RecordDefinitionFacet,
    RecordFacetKind,
    RecordMaterializationSource,
    RecordRecomputeQueueItem,
    RecordRule,
    RecordSeries,
    RecordStanding,
)

_MAPPINGS = (
    (RecordRule.__table__, "record_class", RecordClass),
    (RecordRule.__table__, "build_kind", BuildKind),
    (RecordRule.__table__, "version_scope", VersionScope),
    (RecordRule.__table__, "materialization_source", RecordMaterializationSource),
    (RecordSeries.__table__, "record_class", RecordClass),
    (RecordSeries.__table__, "build_kind", BuildKind),
    (RecordSeries.__table__, "version_scope", VersionScope),
    (RecordDefinitionFacet.__table__, "facet_kind", RecordFacetKind),
    (RecordComputationRun.__table__, "build_kind", BuildKind),
    (RecordComputationRun.__table__, "status", RecordComputationStatus),
    (RecordStanding.__table__, "status", ResolutionStatus),
    (RecordRecomputeQueueItem.__table__, "build_kind", BuildKind),
)

_CHECKS = (
    (RecordRule.__table__, "record_definitions_record_class_check", RecordClass),
    (RecordRule.__table__, "record_definitions_build_kind_check", BuildKind),
    (RecordRule.__table__, "record_definitions_version_scope_check", VersionScope),
    (
        RecordRule.__table__,
        "record_definitions_materialization_source_check",
        RecordMaterializationSource,
    ),
    (RecordSeries.__table__, "record_competitions_record_class_check", RecordClass),
    (RecordSeries.__table__, "record_competitions_build_kind_check", BuildKind),
    (RecordSeries.__table__, "record_competitions_version_scope_check", VersionScope),
    (RecordDefinitionFacet.__table__, "record_definition_facets_kind_check", RecordFacetKind),
    (RecordComputationRun.__table__, "record_computation_runs_build_kind_check", BuildKind),
    (RecordComputationRun.__table__, "record_computation_runs_status_check", RecordComputationStatus),
    (RecordStanding.__table__, "record_results_status_check", ResolutionStatus),
    (RecordRecomputeQueueItem.__table__, "record_recompute_queue_build_kind_check", BuildKind),
)


@pytest.mark.parametrize(("table", "column_name", "enum_type"), _MAPPINGS)
def test_record_text_enums_round_trip_every_member(
    table: Table,
    column_name: str,
    enum_type: type[StrEnum],
) -> None:
    column_type = table.c[column_name].type
    assert isinstance(column_type, StrEnumText)
    dialect = default.DefaultDialect()

    for member in enum_type:
        stored = column_type.process_bind_param(member, dialect)
        assert stored == member.value
        assert column_type.process_result_value(stored, dialect) is member

    with pytest.raises(ValueError, match="not-a-valid-member"):
        column_type.process_result_value("not-a-valid-member", dialect)


@pytest.mark.parametrize(("table", "constraint_name", "enum_type"), _CHECKS)
def test_record_check_constraints_cover_exact_enum_values(
    table: Table,
    constraint_name: str,
    enum_type: type[StrEnum],
) -> None:
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name
    )
    stored_values = set(re.findall(r"'([^']+)'", str(constraint.sqltext)))

    assert stored_values == {member.value for member in enum_type}
