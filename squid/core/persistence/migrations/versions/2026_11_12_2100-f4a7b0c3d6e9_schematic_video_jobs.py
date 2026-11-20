"""Allow durable schematic video rendering jobs.

Revision ID: f4a7b0c3d6e9
Revises: e3f6a9b2c5d8
"""

from alembic import op

revision = "f4a7b0c3d6e9"
down_revision = "e3f6a9b2c5d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("schematic_jobs_operation_check", "schematic_jobs", type_="check")
    op.create_check_constraint(
        "schematic_jobs_operation_check",
        "schematic_jobs",
        "operation IN ('capabilities', 'analyze', 'convert', 'compare', 'render', 'render_video', 'simulate', 'autostack')",
    )


def downgrade() -> None:
    # Retained video jobs must be cleaned up before removing their supported operation.
    op.drop_constraint("schematic_jobs_operation_check", "schematic_jobs", type_="check")
    op.create_check_constraint(
        "schematic_jobs_operation_check",
        "schematic_jobs",
        "operation IN ('capabilities', 'analyze', 'convert', 'compare', 'render', 'simulate', 'autostack')",
    )
