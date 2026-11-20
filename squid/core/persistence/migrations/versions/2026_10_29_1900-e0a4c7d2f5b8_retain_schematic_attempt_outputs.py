"""Retain schematic attempt outputs for expiry cleanup.

Revision ID: e0a4c7d2f5b8
Revises: d9f3b6c1e4a7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e0a4c7d2f5b8"
down_revision: str | Sequence[str] | None = "d9f3b6c1e4a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "schematic_jobs",
        sa.Column(
            "result_object_keys",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
            comment="Result objects registered by worker claims, retained until job expiry.",
        ),
    )


def downgrade() -> None:
    op.drop_column("schematic_jobs", "result_object_keys")
