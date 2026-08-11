"""Add browser-approved CLI device authentication.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-08-11 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hash-only enrollments, devices, proof nonces, and sessions."""
    op.create_table(
        "cli_device_enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("user_code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("public_key", sa.LargeBinary(length=32), nullable=False),
        sa.Column("client_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_account_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(approved_by_account_id IS NULL) = (approved_at IS NULL)",
            name="cli_device_enrollments_approval_complete",
        ),
        sa.CheckConstraint(
            "exchanged_at IS NULL OR approved_at IS NOT NULL",
            name="cli_device_enrollments_exchange_requires_approval",
        ),
        sa.CheckConstraint("expires_at > created_at", name="cli_device_enrollments_expiry_after_creation"),
        sa.CheckConstraint("octet_length(device_code_hash) = 32", name="cli_device_enrollments_device_hash_length"),
        sa.CheckConstraint("char_length(label) BETWEEN 1 AND 80", name="cli_device_enrollments_label_length"),
        sa.CheckConstraint("octet_length(public_key) = 32", name="cli_device_enrollments_public_key_length"),
        sa.CheckConstraint("octet_length(user_code_hash) = 32", name="cli_device_enrollments_user_hash_length"),
        sa.ForeignKeyConstraint(
            ["approved_by_account_id"],
            ["accounts.id"],
            name="cli_device_enrollments_approved_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash", name="cli_device_enrollments_device_code_hash_key"),
        sa.UniqueConstraint("user_code_hash", name="cli_device_enrollments_user_code_hash_key"),
    )
    op.create_table_comment(
        "cli_device_enrollments",
        "A browser-approved enrollment storing only digests of bearer codes.",
        existing_comment=None,
        schema=None,
    )
    op.create_index(
        "cli_device_enrollments_active_client_idx",
        "cli_device_enrollments",
        ["client_instance_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("exchanged_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_table(
        "cli_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(length=32), nullable=False),
        sa.Column("client_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("char_length(label) BETWEEN 1 AND 80", name="cli_devices_label_length"),
        sa.CheckConstraint("last_used_at >= created_at", name="cli_devices_last_used_after_creation"),
        sa.CheckConstraint("octet_length(public_key) = 32", name="cli_devices_public_key_length"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="cli_devices_account_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_key", name="cli_devices_public_key_key"),
    )
    op.create_table_comment(
        "cli_devices",
        "An account-owned Ed25519 CLI device.",
        existing_comment=None,
        schema=None,
    )
    op.create_index("cli_devices_account_idx", "cli_devices", ["account_id", "created_at"], unique=False)
    op.create_index(
        "cli_devices_active_account_idx",
        "cli_devices",
        ["account_id", "last_used_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "cli_session_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nonce_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="cli_session_challenges_consumed_after_creation",
        ),
        sa.CheckConstraint("expires_at > created_at", name="cli_session_challenges_expiry_after_creation"),
        sa.CheckConstraint("octet_length(nonce_hash) = 32", name="cli_session_challenges_nonce_hash_length"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["cli_devices.id"], name="cli_session_challenges_device_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nonce_hash", name="cli_session_challenges_nonce_hash_key"),
    )
    op.create_table_comment(
        "cli_session_challenges",
        "A one-time device proof nonce stored only as a digest.",
        existing_comment=None,
        schema=None,
    )
    op.create_index(
        "cli_session_challenges_active_device_idx",
        "cli_session_challenges",
        ["device_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.create_table(
        "cli_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > issued_at", name="cli_sessions_expiry_after_issue"),
        sa.CheckConstraint("last_seen_at >= issued_at", name="cli_sessions_last_seen_after_issue"),
        sa.CheckConstraint("octet_length(token_hash) = 32", name="cli_sessions_token_hash_length"),
        sa.ForeignKeyConstraint(
            ["device_id"], ["cli_devices.id"], name="cli_sessions_device_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="cli_sessions_token_hash_key"),
    )
    op.create_table_comment(
        "cli_sessions",
        "A short-lived CLI bearer session storing only its token digest.",
        existing_comment=None,
        schema=None,
    )
    op.create_index("cli_sessions_device_idx", "cli_sessions", ["device_id", "expires_at"], unique=False)
    op.create_index(
        "cli_sessions_active_device_idx",
        "cli_sessions",
        ["device_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Remove CLI authorization state."""
    op.execute("LOCK TABLE cli_device_enrollments, cli_devices, cli_session_challenges, cli_sessions IN EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM cli_device_enrollments)
                OR EXISTS (SELECT 1 FROM cli_devices)
                OR EXISTS (SELECT 1 FROM cli_session_challenges)
                OR EXISTS (SELECT 1 FROM cli_sessions)
            THEN
                RAISE EXCEPTION 'cannot downgrade while CLI authorization data is retained';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("cli_sessions_active_device_idx", table_name="cli_sessions")
    op.drop_index("cli_sessions_device_idx", table_name="cli_sessions")
    op.drop_table("cli_sessions")
    op.drop_index("cli_session_challenges_active_device_idx", table_name="cli_session_challenges")
    op.drop_table("cli_session_challenges")
    op.drop_index("cli_devices_active_account_idx", table_name="cli_devices")
    op.drop_index("cli_devices_account_idx", table_name="cli_devices")
    op.drop_table("cli_devices")
    op.drop_index("cli_device_enrollments_active_client_idx", table_name="cli_device_enrollments")
    op.drop_table("cli_device_enrollments")
