"""Tests for schematic persistence schema contracts."""

from sqlalchemy import CheckConstraint

from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.schematics.infrastructure.models import SchematicFile


def test_file_size_constraint_uses_the_fixed_schema_ceiling() -> None:
    constraint = next(
        constraint
        for constraint in SchematicFile.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "schematic_files_size_bounded"
    )

    assert str(constraint.sqltext) == f"byte_size > 0 AND byte_size <= {SCHEMATIC_FILE_SCHEMA_MAX_BYTES}"
