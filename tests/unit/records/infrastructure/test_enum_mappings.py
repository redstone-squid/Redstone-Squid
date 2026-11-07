import re
from enum import StrEnum

import pytest
from sqlalchemy import CheckConstraint, Table
from sqlalchemy.engine import default

from squid.persistence.types import StrEnumText
from squid.records.domain import BaseTitle, BuildKind, ResolutionStatus, VersionScope
from squid.records.models import (
    ComputationRun,
    ComputationStatus,
    DefinitionFacet,
    FacetKind,
    MaterializationSource,
    RecordRecomputeQueueItem,
    RecordTitle,
    TitleDefinition,
    TitleResult,
)

_MAPPINGS = (
    (TitleDefinition.__table__, "materialization_source", MaterializationSource),
    (RecordTitle.__table__, "record_class", BaseTitle),
    (RecordTitle.__table__, "build_kind", BuildKind),
    (RecordTitle.__table__, "version_scope", VersionScope),
    (DefinitionFacet.__table__, "facet_kind", FacetKind),
    (ComputationRun.__table__, "build_kind", BuildKind),
    (ComputationRun.__table__, "status", ComputationStatus),
    (TitleResult.__table__, "status", ResolutionStatus),
    (RecordRecomputeQueueItem.__table__, "build_kind", BuildKind),
)

_CHECKS = (
    (
        TitleDefinition.__table__,
        "record_definitions_materialization_source_check",
        MaterializationSource,
    ),
    (RecordTitle.__table__, "record_competitions_record_class_check", BaseTitle),
    (RecordTitle.__table__, "record_competitions_build_kind_check", BuildKind),
    (RecordTitle.__table__, "record_competitions_version_scope_check", VersionScope),
    (DefinitionFacet.__table__, "record_definition_facets_kind_check", FacetKind),
    (ComputationRun.__table__, "record_computation_runs_build_kind_check", BuildKind),
    (ComputationRun.__table__, "record_computation_runs_status_check", ComputationStatus),
    (TitleResult.__table__, "record_results_status_check", ResolutionStatus),
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
