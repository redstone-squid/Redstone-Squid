"""Mark deleted accounts so their rows can remain as tombstones."""

import sqlalchemy as sa

from alembic import op

revision = "c5e8a1d3f7b2"
down_revision = "b47d2e91c3a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the owner deleted the account; the row stays as an empty tombstone for retained references.",
        ),
    )
    # A tombstone keeps no consent receipt: nobody can act as it again.
    op.create_check_constraint(
        "accounts_tombstone_has_no_receipt",
        "accounts",
        "deleted_at IS NULL OR privacy_notice_version IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("accounts_tombstone_has_no_receipt", "accounts", type_="check")
    op.drop_column("accounts", "deleted_at")
