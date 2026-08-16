"""Rename tag_definitions.numeric_quantum to numeric_step.

A "quantum" is a physics word. The column holds the increment a numeric tag value must align
to -- `squid/search/application/fields.py` rejects a value whose modulus against it is non-zero
-- which is a step, and every UI that renders it calls it one.

The API field is renamed with it rather than mapped across, because a split vocabulary between
the wire and the table is the debt this review is about: whoever reads `numeric_step` in a
response and greps for it should land on the column.

`ALTER TABLE ... RENAME COLUMN` rewrites the three check constraints that mention the column
automatically, but not their names, so `tag_definitions_numeric_quantum_check` is renamed
explicitly. The other two constraint names do not mention the column and are left alone.

Revision ID: c9d2e3f4a5b6
Revises: c6d7e8f9a0b1
Create Date: 2026-08-16 11:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("tag_definitions", "numeric_quantum", new_column_name="numeric_step")
    op.execute(
        "ALTER TABLE tag_definitions "
        "RENAME CONSTRAINT tag_definitions_numeric_quantum_check TO tag_definitions_numeric_step_check"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tag_definitions "
        "RENAME CONSTRAINT tag_definitions_numeric_step_check TO tag_definitions_numeric_quantum_check"
    )
    op.alter_column("tag_definitions", "numeric_step", new_column_name="numeric_quantum")
