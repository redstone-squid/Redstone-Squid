"""Flag error reports that lost work

The store now also follows the logs, because the worker's queue consumers absorb a failure,
dead-letter the job, log it and carry on -- nothing reaches the supervisor that would have
captured it, so its failures were the ones an operator could see in the container output and
then not look up.

Following the logs means most reports are failures something recovered from. A dead-lettered
job is not: nothing will retry it, and a build's search document or a schematic's render simply
never appears. `work_lost` separates the two, so a hundred recovered exceptions cannot bury the
one that actually cost something.

Defaulted rather than backfilled: every existing row predates log-driven capture and came from a
transport handler, none of which abandons work.

Revision ID: f68bbbd25847
Revises: 970f592fd57c
Create Date: 2026-08-17 02:34:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f68bbbd25847"
down_revision: str | Sequence[str] | None = "970f592fd57c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.add_column(
        "error_reports",
        sa.Column(
            "work_lost",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            # Mirrors the model's attribute docstring, which `Base` turns into a column comment.
            comment="Whether this failure permanently abandoned work, as a dead-lettered job does.",
        ),
    )
    op.create_index(
        "error_reports_work_lost_idx",
        "error_reports",
        ["occurred_at"],
        unique=False,
        postgresql_where=sa.text("work_lost"),
    )


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.drop_index("error_reports_work_lost_idx", table_name="error_reports")
    op.drop_column("error_reports", "work_lost")
