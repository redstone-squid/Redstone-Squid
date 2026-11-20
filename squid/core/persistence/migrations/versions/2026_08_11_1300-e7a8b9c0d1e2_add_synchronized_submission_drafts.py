"""Add synchronized submission drafts.

Revision ID: e7a8b9c0d1e2
Revises: d6f7a8b9c0d1
Create Date: 2026-08-11 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "d6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_account_id", sa.Integer(), nullable=False),
        sa.Column("schema_id", sa.Text(), nullable=False),
        sa.Column("schema_revision", sa.Integer(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'editing'"), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expires_at > created_at", name="submission_drafts_expiry_after_creation"),
        sa.CheckConstraint(
            "origin IN ('discord', 'web', 'paper', 'fabric')",
            name="submission_drafts_origin_check",
        ),
        sa.CheckConstraint("revision >= 0", name="submission_drafts_revision_nonnegative"),
        sa.CheckConstraint("schema_revision > 0", name="submission_drafts_schema_revision_positive"),
        sa.CheckConstraint(
            "status IN ('editing', 'processing', 'needs_attention', 'submitted', 'expired')",
            name="submission_drafts_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["owner_account_id"],
            ["accounts.id"],
            name="submission_drafts_owner_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="The compact current state of an account-owned submission draft.",
    )
    op.create_index(
        "submission_drafts_expiry_idx",
        "submission_drafts",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('editing', 'processing', 'needs_attention')"),
    )
    op.create_index(
        "submission_drafts_owner_updated_idx",
        "submission_drafts",
        ["owner_account_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "submission_draft_access",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'editor')", name="submission_draft_access_role_check"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="submission_draft_access_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["submission_drafts.id"],
            name="submission_draft_access_draft_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "account_id", name="submission_draft_access_draft_account_key"),
        comment="An account's role on a draft; v1 creates exactly one owner grant.",
    )
    op.create_index(
        "submission_draft_access_account_idx",
        "submission_draft_access",
        ["account_id", "draft_id"],
        unique=False,
    )
    op.create_index(
        "submission_draft_access_one_owner",
        "submission_draft_access",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )

    op.create_table(
        "submission_draft_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_revision", sa.Integer(), nullable=False),
        sa.Column("client_instance_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("base_revision >= 0", name="submission_draft_changes_base_revision_nonnegative"),
        sa.CheckConstraint("jsonb_array_length(operations) > 0", name="submission_draft_changes_has_operations"),
        sa.CheckConstraint(
            "resulting_revision = base_revision + 1",
            name="submission_draft_changes_revision_sequence",
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["accounts.id"],
            name="submission_draft_changes_actor_account_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["submission_drafts.id"],
            name="submission_draft_changes_draft_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "idempotency_key", name="submission_draft_changes_draft_idempotency_key"),
        sa.UniqueConstraint("draft_id", "resulting_revision", name="submission_draft_changes_draft_revision_key"),
        comment="An immutable accepted field-operation batch used for retries and audit.",
    )
    op.create_index(
        "submission_draft_changes_actor_idx",
        "submission_draft_changes",
        ["actor_account_id", "applied_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("submission_draft_changes_actor_idx", table_name="submission_draft_changes")
    op.drop_table("submission_draft_changes")
    op.drop_index("submission_draft_access_one_owner", table_name="submission_draft_access")
    op.drop_index("submission_draft_access_account_idx", table_name="submission_draft_access")
    op.drop_table("submission_draft_access")
    op.drop_index("submission_drafts_owner_updated_idx", table_name="submission_drafts")
    op.drop_index("submission_drafts_expiry_idx", table_name="submission_drafts")
    op.drop_table("submission_drafts")
