"""Tests for schematic persistence schema contracts."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.schematics.models import SchematicFile, SchematicPreviewObject, SchematicRender
from tests.support.schema import model_table


def test_file_size_constraint_uses_the_fixed_schema_ceiling() -> None:
    table = model_table(SchematicFile)
    constraint = next(
        constraint
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "schematic_files_size_bounded"
    )

    assert str(constraint.sqltext) == f"byte_size > 0 AND byte_size <= {SCHEMATIC_FILE_SCHEMA_MAX_BYTES}"


def test_render_objects_have_durable_lifecycle_rows() -> None:
    object_table = model_table(SchematicPreviewObject)
    render_table = model_table(SchematicRender)
    foreign_key = next(
        constraint
        for constraint in render_table.constraints
        if isinstance(constraint, ForeignKeyConstraint) and constraint.name == "schematic_renders_object_key_fkey"
    )

    assert object_table.primary_key.columns.keys() == ["object_key"]
    assert [element.target_fullname for element in foreign_key.elements] == ["schematic_preview_objects.object_key"]
    assert foreign_key.ondelete == "RESTRICT"
