"""Fence preview cleanup outside database transactions.

Revision ID: b3d7f0a5c8e1
Revises: a2c6e9f4b7d0
"""

import sqlalchemy as sa

from alembic import op

revision: str = "b3d7f0a5c8e1"
down_revision: str | None = "a2c6e9f4b7d0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "schematic_preview_objects",
        sa.Column(
            "cleanup_claimed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When cleanup reserved this unreferenced object for deletion.",
        ),
    )
    op.add_column(
        "schematic_preview_objects",
        sa.Column(
            "cleanup_claim_token",
            sa.UUID(),
            nullable=True,
            comment="Fence shared by cleanup acknowledgement and publication checks.",
        ),
    )
    op.create_check_constraint(
        "schematic_preview_objects_cleanup_claim_pair",
        "schematic_preview_objects",
        "(cleanup_claimed_at IS NULL) = (cleanup_claim_token IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("schematic_preview_objects_cleanup_claim_pair", "schematic_preview_objects", type_="check")
    op.drop_column("schematic_preview_objects", "cleanup_claim_token")
    op.drop_column("schematic_preview_objects", "cleanup_claimed_at")
