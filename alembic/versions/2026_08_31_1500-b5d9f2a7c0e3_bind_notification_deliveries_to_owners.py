"""Bind notification deliveries to the notification owner.

Revision ID: b5d9f2a7c0e3
Revises: a4c8e1f6b9d2
Create Date: 2026-08-31 15:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b5d9f2a7c0e3"
down_revision: str | Sequence[str] | None = "a4c8e1f6b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Reject deliveries whose redundant account owner disagrees with the notification."""
    op.create_unique_constraint(
        "notifications_id_account_key",
        "notifications",
        ["id", "account_id"],
    )
    op.create_foreign_key(
        "notification_deliveries_notification_owner_fkey",
        "notification_deliveries",
        "notifications",
        ["notification_id", "account_id"],
        ["id", "account_id"],
        ondelete="CASCADE",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE notification_deliveries VALIDATE CONSTRAINT notification_deliveries_notification_owner_fkey"
    )
    op.drop_constraint(
        "notification_deliveries_notification_id_fkey",
        "notification_deliveries",
        type_="foreignkey",
    )


def downgrade() -> None:
    """Restore the notification-only foreign key while preserving cascade deletion."""
    op.create_foreign_key(
        "notification_deliveries_notification_id_fkey",
        "notification_deliveries",
        "notifications",
        ["notification_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "notification_deliveries_notification_owner_fkey",
        "notification_deliveries",
        type_="foreignkey",
    )
    op.drop_constraint("notifications_id_account_key", "notifications", type_="unique")
