"""Retain private schematic intake and independent draft references.

Revision ID: b1d5f8a3c6e9
Revises: a0c4e7f2b5d8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b1d5f8a3c6e9"
down_revision: str | Sequence[str] | None = "a0c4e7f2b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Track bytes before storage and fence cleanup against retained references."""
    op.create_table(
        "submission_schematic_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "uploaded_by_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "byte_size IS NULL OR (byte_size > 0 AND byte_size <= 16777216)",
            name="submission_schematic_sources_size_check",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="submission_schematic_sources_sha_check"),
        sa.CheckConstraint(
            "state IN ('uploading', 'waiting_sanitizer', 'failed', 'deleting', 'deleted')",
            name="submission_schematic_sources_state_check",
        ),
        sa.CheckConstraint(
            "state <> 'waiting_sanitizer' OR (sha256 IS NOT NULL AND byte_size IS NOT NULL)",
            name="submission_schematic_sources_uploaded_check",
        ),
        comment="Private source bytes tracked before upload and retained until reference-safe deletion.",
    )
    for suffix, columns in (("owner", ["owner_account_id"]), ("actor", ["uploaded_by_account_id"])):
        op.create_index(f"submission_schematic_sources_{suffix}_idx", "submission_schematic_sources", columns)
    op.create_index(
        "submission_schematic_sources_cleanup_idx",
        "submission_schematic_sources",
        ["updated_at"],
        postgresql_where=sa.text("state <> 'deleted'"),
    )
    op.create_table(
        "submission_draft_schematics",
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_drafts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submission_schematic_sources.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("discarded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "NOT discarded OR NOT is_primary", name="submission_draft_schematics_discarded_primary_check"
        ),
        comment="One draft's retained schematic reference and explicit primary selection.",
    )
    op.create_index("submission_draft_schematics_source_idx", "submission_draft_schematics", ["source_id"])
    op.create_index(
        "submission_draft_schematics_one_primary",
        "submission_draft_schematics",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    """Do not orphan retained private source objects by removing their tracking."""
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM submission_schematic_sources WHERE state <> 'deleted')")
    ):
        message = "Cannot downgrade while private schematic sources are retained."
        raise RuntimeError(message)
    op.drop_table("submission_draft_schematics")
    op.drop_table("submission_schematic_sources")
