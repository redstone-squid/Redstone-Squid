"""Remember catalogue filters and sort order."""

import sqlalchemy as sa

from alembic import op

revision = "a325bc2746d1"
down_revision = "b8d2f6a0e415"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalogue_preferences",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scope", sa.Text(), primary_key=True),
        sa.Column("filters", sa.Text(), nullable=False),
        sa.Column("sort", sa.Text(), nullable=False),
        sa.CheckConstraint("scope IN ('builds', 'records')", name="catalogue_preferences_scope_check"),
        comment="Saved catalogue filters and sort order for an account and tab.",
    )


def downgrade() -> None:
    op.drop_table("catalogue_preferences")
