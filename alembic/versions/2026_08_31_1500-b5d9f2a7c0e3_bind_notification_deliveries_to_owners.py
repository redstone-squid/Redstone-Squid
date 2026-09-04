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
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM notification_deliveries AS delivery
                LEFT JOIN notifications AS notification
                  ON notification.id = delivery.notification_id
                 AND notification.account_id = delivery.account_id
                WHERE notification.id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'notification delivery ownership mismatch blocks composite foreign key';
            END IF;
        END
        $$
        """
    )
    # The inbox is retained independently of worker deploys and can be large. Build the
    # supporting index without holding a write lock, then attach it as the FK target. A
    # crash after this autocommit block leaves only a standalone index; dropping it first
    # makes the migration restart-safe and also replaces PostgreSQL's INVALID concurrent
    # index artifact after an interrupted build.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS notifications_id_account_key")
        op.create_index(
            "notifications_id_account_key",
            "notifications",
            ["id", "account_id"],
            unique=True,
            postgresql_concurrently=True,
        )
    op.create_foreign_key(
        "notification_deliveries_notification_owner_fkey",
        "notification_deliveries",
        "notifications",
        ["notification_id", "account_id"],
        ["id", "account_id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE notification_deliveries VALIDATE CONSTRAINT notification_deliveries_notification_owner_fkey"
    )
    # The FK can use the standalone unique index while it validates. Attach only
    # after that scan so the parent table's ACCESS EXCLUSIVE lock stays brief.
    op.execute(
        "ALTER TABLE notifications ADD CONSTRAINT notifications_id_account_key "
        "UNIQUE USING INDEX notifications_id_account_key"
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
