"""Drop edit resource leases; open drafts now keep files they have removed."""

from alembic import op

revision = "d2e5f8a1b4c7"
down_revision = "e3a6c9f2b5d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("edit_resource_leases")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE edit_resource_leases (
            operation_id uuid NOT NULL,
            resource text NOT NULL,
            expires_at timestamptz NOT NULL,
            PRIMARY KEY (operation_id, resource)
        );
        CREATE INDEX edit_resource_leases_resource_expiry_idx ON edit_resource_leases (resource, expires_at);
    """)
