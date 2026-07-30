"""Establish the portable application schema.

Revision ID: 20260728_baseline
Revises:
Create Date: 2026-07-28
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "20260728_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "20260728_portable_schema.sql"


def upgrade() -> None:
    """Create the complete application-owned PostgreSQL schema."""
    schema_sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    driver_connection = op.get_bind().connection.driver_connection
    cursor = driver_connection.cursor()
    try:
        cursor.execute(schema_sql)
    finally:
        cursor.close()


def downgrade() -> None:
    """Prevent accidental destruction of the complete application schema."""
    msg = "The baseline revision cannot be downgraded; restore a backup or recreate the database instead."
    raise RuntimeError(msg)
