"""Add Minecraft device authorization.

Revision ID: d2f3a4b5c6d7
Revises: c1e2f3a4b5c6
Create Date: 2026-08-11 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "c1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist Paper credentials and one-time player device authorization."""
    op.create_table(
        "minecraft_paper_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_account_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("credential_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("public_profile_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("public_display_name", sa.Text(), nullable=True),
        sa.Column("public_address", sa.Text(), nullable=True),
        sa.Column("public_description", sa.Text(), nullable=True),
        sa.Column("public_website_url", sa.Text(), nullable=True),
        sa.Column("sponsor_opt_in", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(label) BETWEEN 1 AND 80",
            name="minecraft_paper_installations_label_length",
        ),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32",
            name="minecraft_paper_installations_secret_hash_length",
        ),
        sa.CheckConstraint(
            "credential_version >= 1",
            name="minecraft_paper_installations_version_positive",
        ),
        sa.CheckConstraint(
            "public_display_name IS NULL OR char_length(public_display_name) BETWEEN 1 AND 80",
            name="minecraft_paper_installations_display_name_length",
        ),
        sa.CheckConstraint(
            "public_address IS NULL OR char_length(public_address) BETWEEN 1 AND 255",
            name="minecraft_paper_installations_address_length",
        ),
        sa.CheckConstraint(
            "public_description IS NULL OR char_length(public_description) BETWEEN 1 AND 500",
            name="minecraft_paper_installations_description_length",
        ),
        sa.CheckConstraint(
            "public_website_url IS NULL OR char_length(public_website_url) BETWEEN 1 AND 2048",
            name="minecraft_paper_installations_website_length",
        ),
        sa.CheckConstraint(
            "NOT sponsor_opt_in OR public_profile_enabled",
            name="minecraft_paper_installations_sponsor_requires_public",
        ),
        sa.ForeignKeyConstraint(
            ["owner_account_id"],
            ["accounts.id"],
            name="minecraft_paper_installations_owner_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="An account-owned Paper installation with a non-recoverable credential.",
    )
    op.create_index(
        "minecraft_paper_installations_owner_idx",
        "minecraft_paper_installations",
        ["owner_account_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "minecraft_paper_installations_public_idx",
        "minecraft_paper_installations",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("public_profile_enabled AND revoked_at IS NULL"),
    )

    op.create_table(
        "minecraft_player_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("user_code_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("java_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installation_credential_version", sa.Integer(), nullable=True),
        sa.Column("pkce_s256_challenge", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_account_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(device_code_hash) = 32",
            name="minecraft_player_challenges_device_hash_length",
        ),
        sa.CheckConstraint(
            "octet_length(user_code_hash) = 32",
            name="minecraft_player_challenges_user_hash_length",
        ),
        sa.CheckConstraint(
            "origin IN ('paper', 'fabric')",
            name="minecraft_player_challenges_origin_check",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="minecraft_player_challenges_expiry_after_creation",
        ),
        sa.CheckConstraint(
            "(approved_by_account_id IS NULL) = (approved_at IS NULL)",
            name="minecraft_player_challenges_approval_complete",
        ),
        sa.CheckConstraint(
            "exchanged_at IS NULL OR approved_at IS NOT NULL",
            name="minecraft_player_challenges_exchange_requires_approval",
        ),
        sa.CheckConstraint(
            "(origin = 'paper' AND installation_id IS NOT NULL AND installation_credential_version IS NOT NULL "
            "AND pkce_s256_challenge IS NULL) OR "
            "(origin = 'fabric' AND installation_id IS NULL AND installation_credential_version IS NULL "
            "AND pkce_s256_challenge IS NOT NULL)",
            name="minecraft_player_challenges_origin_binding",
        ),
        sa.CheckConstraint(
            "pkce_s256_challenge IS NULL OR pkce_s256_challenge ~ '^[A-Za-z0-9_-]{43}$'",
            name="minecraft_player_challenges_pkce_format",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_account_id"],
            ["accounts.id"],
            name="minecraft_player_challenges_approved_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["minecraft_paper_installations.id"],
            name="minecraft_player_challenges_installation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash", name="minecraft_player_challenges_device_code_hash_key"),
        sa.UniqueConstraint("user_code_hash", name="minecraft_player_challenges_user_code_hash_key"),
        comment="A short-lived device flow storing only hashes of both bearer codes.",
    )
    op.create_index(
        "minecraft_player_challenges_active_lookup_idx",
        "minecraft_player_challenges",
        ["origin", "java_uuid", "installation_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("exchanged_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "minecraft_player_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("challenge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("java_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installation_credential_version", sa.Integer(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(token_hash) = 32",
            name="minecraft_player_grants_token_hash_length",
        ),
        sa.CheckConstraint(
            "origin IN ('paper', 'fabric')",
            name="minecraft_player_grants_origin_check",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="minecraft_player_grants_expiry_after_issue",
        ),
        sa.CheckConstraint(
            "(origin = 'paper' AND installation_id IS NOT NULL AND installation_credential_version IS NOT NULL) OR "
            "(origin = 'fabric' AND installation_id IS NULL AND installation_credential_version IS NULL)",
            name="minecraft_player_grants_origin_binding",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="minecraft_player_grants_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["challenge_id"],
            ["minecraft_player_challenges.id"],
            name="minecraft_player_grants_challenge_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["minecraft_paper_installations.id"],
            name="minecraft_player_grants_installation_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_id", name="minecraft_player_grants_challenge_id_key"),
        sa.UniqueConstraint("token_hash", name="minecraft_player_grants_token_hash_key"),
        comment="A short-lived origin- and identity-bound player bearer grant.",
    )
    op.create_index(
        "minecraft_player_grants_account_idx",
        "minecraft_player_grants",
        ["account_id", "expires_at"],
        unique=False,
    )
    op.create_index(
        "minecraft_player_grants_active_installation_idx",
        "minecraft_player_grants",
        ["installation_id", "expires_at"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    """Remove Minecraft authorization only while no credential state exists."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM minecraft_paper_installations)
                OR EXISTS (SELECT 1 FROM minecraft_player_challenges)
                OR EXISTS (SELECT 1 FROM minecraft_player_grants) THEN
                RAISE EXCEPTION 'cannot downgrade while Minecraft authorization state is retained';
            END IF;
        END;
        $$
        """
    )
    op.drop_index("minecraft_player_grants_active_installation_idx", table_name="minecraft_player_grants")
    op.drop_index("minecraft_player_grants_account_idx", table_name="minecraft_player_grants")
    op.drop_table("minecraft_player_grants")
    op.drop_index("minecraft_player_challenges_active_lookup_idx", table_name="minecraft_player_challenges")
    op.drop_table("minecraft_player_challenges")
    op.drop_index("minecraft_paper_installations_public_idx", table_name="minecraft_paper_installations")
    op.drop_index("minecraft_paper_installations_owner_idx", table_name="minecraft_paper_installations")
    op.drop_table("minecraft_paper_installations")
