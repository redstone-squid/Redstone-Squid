"""Dead-letter exhausted search projections.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-10 18:00:00+00:00
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction

from alembic import op
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {
    "enqueue_build_search_projection",
    "enqueue_metadata_search_projection",
    "enqueue_computed_record_search_projection",
}


def upgrade() -> None:
    """Retain exhausted work and resurrect it only when source state changes."""
    op.add_column("search_projection_queue", sa.Column("dead_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.drop_index("search_projection_queue_ready_idx", table_name="search_projection_queue")
    op.create_index(
        "search_projection_queue_ready_idx",
        "search_projection_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("locked_at IS NULL AND dead_at IS NULL"),
    )
    for entity in _search_projection_functions():
        for statement in entity.to_sql_statement_create_or_replace():
            op.execute(statement)


def downgrade() -> None:
    """Restore unlimited retries and the previous enqueue functions."""
    for entity in _search_projection_functions():
        for statement in entity.to_sql_statement_create_or_replace():
            old_definition = re.sub(r"dead_at = NULL,\s*", "", str(statement))
            op.execute(old_definition)
    op.drop_index("search_projection_queue_ready_idx", table_name="search_projection_queue")
    op.create_index(
        "search_projection_queue_ready_idx",
        "search_projection_queue",
        ["enqueued_at"],
        postgresql_where=sa.text("locked_at IS NULL"),
    )
    op.drop_column("search_projection_queue", "dead_at")


def _search_projection_functions() -> list[PGFunction]:
    return [
        entity
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] in _FUNCTION_NAMES
    ]
