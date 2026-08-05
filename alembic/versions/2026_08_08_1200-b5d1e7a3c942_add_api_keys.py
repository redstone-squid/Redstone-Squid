"""Add scoped API keys.

Revision ID: b5d1e7a3c942
Revises: a4c8e2f6b913
Create Date: 2026-08-08 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b5d1e7a3c942"
down_revision: str | Sequence[str] | None = "a4c8e2f6b913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create revocable, scoped service credentials."""
    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="api_keys_owner_user_id_fkey", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="api_keys_created_by_fkey"),
        sa.PrimaryKeyConstraint("id"),
        comment="A revocable high-entropy credential used by an API service client.",
    )
    op.create_index("api_keys_key_id_key", "api_keys", ["key_id"], unique=True)
    op.create_index(
        "api_keys_active",
        "api_keys",
        ["key_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Remove service credentials."""
    op.drop_index("api_keys_active", table_name="api_keys")
    op.drop_index("api_keys_key_id_key", table_name="api_keys")
    op.drop_table("api_keys")
