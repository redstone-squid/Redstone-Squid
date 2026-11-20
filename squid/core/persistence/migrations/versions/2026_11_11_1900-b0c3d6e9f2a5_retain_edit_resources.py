"""Retain attachment resources while session history can restore them."""

from alembic import op

revision = "b0c3d6e9f2a5"
down_revision = "a9b2c5d8e1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE submission_supplied_attachments ADD COLUMN discarded boolean NOT NULL DEFAULT false;
        UPDATE submission_supplied_attachments SET discarded = true WHERE status = 'discarded';
        CREATE TABLE edit_resource_leases (
            operation_id uuid NOT NULL,
            resource text NOT NULL,
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (operation_id, resource)
        );
        CREATE INDEX edit_resource_leases_resource_expiry_idx ON edit_resource_leases (resource, expires_at);
    """)


def downgrade() -> None:
    op.execute("UPDATE submission_supplied_attachments SET status = 'discarded' WHERE discarded")
    op.drop_column("submission_supplied_attachments", "discarded")
    op.drop_table("edit_resource_leases")
