"""Track the follows required for a Discord notification delivery.

Revision ID: a1cf4b96d235
Revises: 6d9a04bcc27f
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "a1cf4b96d235"
down_revision = "6d9a04bcc27f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_deliveries", sa.Column("subscription_ids", ARRAY(sa.BigInteger())))


def downgrade() -> None:
    op.drop_column("notification_deliveries", "subscription_ids")
