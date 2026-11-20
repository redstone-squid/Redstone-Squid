"""store application timestamps as UTC instants

Revision ID: 3e191f0adfcf
Revises: 7a40995322dc
Create Date: 2026-07-30 00:25:35.857039+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "3e191f0adfcf"
down_revision: str | Sequence[str] | None = "7a40995322dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    _make_timestamp_aware("users", "created_at")
    _make_timestamp_aware("verification_codes", "created")
    _make_timestamp_aware("verification_codes", "expires")
    _make_timestamp_aware("builds", "submission_time")
    op.alter_column(
        "builds",
        "edited_time",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.alter_column(
        "builds",
        "edited_time",
        existing_type=sa.DateTime(timezone=True),
        server_default=sa.text("(now() AT TIME ZONE 'utc'::text)"),
        existing_nullable=True,
    )
    _make_timestamp_naive("builds", "submission_time")
    _make_timestamp_naive("verification_codes", "expires")
    _make_timestamp_naive("verification_codes", "created")
    _make_timestamp_naive("users", "created_at")


def _make_timestamp_aware(table_name: str, column_name: str) -> None:
    op.alter_column(
        table_name,
        column_name,
        existing_type=sa.DateTime(timezone=False),
        type_=sa.DateTime(timezone=True),
        postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        existing_nullable=_is_nullable(table_name, column_name),
    )


def _make_timestamp_naive(table_name: str, column_name: str) -> None:
    op.alter_column(
        table_name,
        column_name,
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(timezone=False),
        postgresql_using=f"{column_name} AT TIME ZONE 'UTC'",
        existing_nullable=_is_nullable(table_name, column_name),
    )


def _is_nullable(table_name: str, column_name: str) -> bool:
    return (table_name, column_name) in {("users", "created_at"), ("builds", "submission_time")}
