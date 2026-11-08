"""Drop submission_draft_access and tag_relations, which nothing reads.

Draft access is decided by submission_drafts.owner_account_id alone, and tag relations were never
written or read outside their migrations.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a4d7c0f5b2e8"
down_revision: str | None = "f3c6b9e4a1d7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("submission_draft_access")
    op.drop_table("tag_relations")


def downgrade() -> None:
    op.create_table(
        "tag_relations",
        sa.Column("source_tag_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_kind", sa.Text(), nullable=False),
        sa.Column("target_tag_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("relation_kind IN ('implies', 'incompatible')", name="tag_relations_kind_check"),
        sa.CheckConstraint("source_tag_id <> target_tag_id", name="tag_relations_distinct_check"),
        sa.ForeignKeyConstraint(
            ["source_tag_id"], ["tag_definitions.id"], name="tag_relations_source_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_tag_id"], ["tag_definitions.id"], name="tag_relations_target_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("source_tag_id", "relation_kind", "target_tag_id", name="tag_relations_pkey"),
        comment="A semantic relationship between official restrictions.",
    )
    op.create_index("tag_relations_target_idx", "tag_relations", ["target_tag_id"])
    op.create_table(
        "submission_draft_access",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'editor')", name="submission_draft_access_role_check"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="submission_draft_access_account_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["submission_drafts.id"], name="submission_draft_access_draft_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="submission_draft_access_pkey"),
        sa.UniqueConstraint("draft_id", "account_id", name="submission_draft_access_draft_account_key"),
        comment="An account's role on a draft; creation grants one account the owner role.",
    )
    op.create_index("submission_draft_access_account_idx", "submission_draft_access", ["account_id", "draft_id"])
    op.create_index(
        "submission_draft_access_one_owner",
        "submission_draft_access",
        ["draft_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )
    # Restore the owner grant every draft had before the table was dropped.
    op.execute(
        "INSERT INTO submission_draft_access (draft_id, account_id, role) "
        "SELECT id, owner_account_id, 'owner' FROM submission_drafts WHERE owner_account_id IS NOT NULL"
    )
