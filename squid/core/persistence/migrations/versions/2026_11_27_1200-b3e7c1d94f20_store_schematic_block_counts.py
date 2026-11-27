"""Store schematic block counts and name the attachment time.

The API's schematic summary charts the most common blocks, which the analysis
computed but did not store. Attachments analyzed earlier keep NULL counts until
they are analyzed again.

analyzed_at was only ever set on insert, because reanalysis upserts the row
without touching it, so it is renamed to attached_at to say what it holds.

Revision ID: b3e7c1d94f20
Revises: a82c82d78b61
Create Date: 2026-11-27 12:00:00+00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3e7c1d94f20"
down_revision: str | None = "a82c82d78b61"
branch_labels: str | None = None
depends_on: str | None = None


_BLOCK_COUNTS = "Non-air blocks per block state, or None for attachments analyzed before counts were stored."
_ATTACHED_AT = "When the file was first attached to this build; reanalysis leaves it unchanged."


def upgrade() -> None:
    op.add_column(
        "build_schematics", sa.Column("block_counts", postgresql.JSONB(), nullable=True, comment=_BLOCK_COUNTS)
    )
    op.alter_column("build_schematics", "analyzed_at", new_column_name="attached_at", comment=_ATTACHED_AT)


def downgrade() -> None:
    op.alter_column("build_schematics", "attached_at", new_column_name="analyzed_at")
    op.drop_column("build_schematics", "block_counts")
