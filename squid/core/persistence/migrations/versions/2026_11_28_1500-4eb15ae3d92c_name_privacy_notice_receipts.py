"""Name existing receipts for the privacy notice they actually identify."""

from alembic import op

revision = "4eb15ae3d92c"
down_revision = "b51eaec4579f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("accounts", "consent_version", new_column_name="privacy_notice_version")
    op.alter_column("accounts", "consented_at", new_column_name="privacy_notice_acknowledged_at")
    op.execute(
        "ALTER TABLE accounts RENAME CONSTRAINT accounts_consent_receipt_complete TO accounts_privacy_notice_receipt_complete"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE accounts RENAME CONSTRAINT accounts_privacy_notice_receipt_complete TO accounts_consent_receipt_complete"
    )
    op.alter_column("accounts", "privacy_notice_acknowledged_at", new_column_name="consented_at")
    op.alter_column("accounts", "privacy_notice_version", new_column_name="consent_version")
