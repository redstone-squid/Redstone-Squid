"""add remaining record classes

Revision ID: b4e7c1a93d52
Revises: 8d7c2e4a91b6
Create Date: 2026-07-30 18:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4e7c1a93d52"
down_revision: str | Sequence[str] | None = "8d7c2e4a91b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow every record class defined by the rules."""
    op.drop_constraint("record_definitions_record_class_check", "record_definitions", type_="check")
    op.create_check_constraint(
        "record_definitions_record_class_check",
        "record_definitions",
        "record_class IN ('first', 'fastest', 'smallest', 'fastest_smallest', 'smallest_fastest')",
    )


def downgrade() -> None:
    """Restore the original record-class set."""
    op.execute(
        """
        DELETE FROM record_definitions
        WHERE record_class IN ('first', 'fastest_smallest', 'smallest_fastest')
        """
    )
    op.drop_constraint("record_definitions_record_class_check", "record_definitions", type_="check")
    op.create_check_constraint(
        "record_definitions_record_class_check",
        "record_definitions",
        "record_class IN ('smallest', 'fastest')",
    )
