"""Add optimistic build revisions.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-10 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction

from alembic import op
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVISION_UPDATE = "SET    extra_info = a.new_extra,\n               revision = b.revision + 1"
_LEGACY_UPDATE = "SET    extra_info = a.new_extra"


def upgrade() -> None:
    """Add and backfill a monotonic build aggregate revision."""
    op.add_column(
        "builds",
        sa.Column("revision", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.execute(
        """
        UPDATE builds b
        SET revision = history.revision
        FROM (
            SELECT build_id, GREATEST(max(version)::bigint + 1, 1) AS revision
            FROM build_edit_history
            GROUP BY build_id
        ) history
        WHERE b.id = history.build_id
        """
    )
    op.create_check_constraint("builds_revision_positive", "builds", "revision > 0")
    op.execute(_restriction_sync_sql())


def downgrade() -> None:
    """Remove build revisions and restore the legacy restriction function."""
    op.execute(_restriction_sync_sql().replace(_REVISION_UPDATE, _LEGACY_UPDATE))
    op.drop_constraint("builds_revision_positive", "builds", type_="check")
    op.drop_column("builds", "revision")


def _restriction_sync_sql() -> str:
    for entity in ALEMBIC_UTIL_ENTITIES:
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] == "sync_new_restriction":
            statement = str(entity.to_sql_statement_create())
            return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    msg = "sync_new_restriction function is missing from the Alembic entity registry"
    raise RuntimeError(msg)
