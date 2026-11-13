"""Drop finalization result compatibility metadata.

Drain finalization workers before upgrading: older ORM mappings can still include
these columns in inserts. Downgrade before restarting older binaries. Downgrade
restores nullable columns, but cannot recover discarded diagnostic metadata.

Revision ID: f45a62f38431
Revises: e34951e27320
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "f45a62f38431"
down_revision: str = "e34951e27320"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint(
        "submission_finalization_results_target_key_check", "submission_finalization_results", type_="check"
    )
    op.drop_column("submission_finalization_results", "target_key")
    op.drop_column("submission_finalization_results", "provenance")


def downgrade() -> None:
    op.add_column("submission_finalization_results", sa.Column("target_key", sa.Text(), nullable=True))
    op.add_column("submission_finalization_results", sa.Column("provenance", JSONB(), nullable=True))
    op.create_check_constraint(
        "submission_finalization_results_target_key_check",
        "submission_finalization_results",
        "target_key IS NULL OR target_key ~ '^[a-z][a-z0-9_]{0,63}$'",
    )
