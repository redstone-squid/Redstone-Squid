from typing import cast

from sqlalchemy import CheckConstraint, Index, Table

from squid.records.application.models import CategoryIdentity
from squid.records.domain import BuildKind, RecordClass
from squid.records.infrastructure.models import RecordComputationRun, RecordDefinition
from squid.records.infrastructure.repository import CALCULATOR_VERSION, FORMATTER_VERSION, parse_category_key


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
