"""Add OAuth state and opaque web sessions.

Revision ID: d7f3a9c5e164
Revises: c6e2f8b4d053
Create Date: 2026-08-08 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7f3a9c5e164"
down_revision: str | Sequence[str] | None = "c6e2f8b4d053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create replica-safe OAuth state and revocable browser sessions."""
    op.create_table(
        "oauth_states",
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("redirect_to", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state"),
        comment="One-time OAuth PKCE state shared across API replicas.",
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="web_sessions_user_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="web_sessions_token_hash_key"),
        comment="A revocable opaque browser session.",
    )
    op.create_index(
        "web_sessions_active_idx",
        "web_sessions",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Remove browser sessions and pending OAuth state."""
    op.drop_index("web_sessions_active_idx", table_name="web_sessions")
    op.drop_table("web_sessions")
    op.drop_table("oauth_states")
