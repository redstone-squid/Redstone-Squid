"""Drop the legacy permission tiers.

The four tiers are gone from the application: every check resolves permission
nodes, and the tier rows were backfilled into role assignments by revision
`e9f0a1b2c3d4`. This removes what is left of them.

`server_settings.trusted_roles_ids` goes with `global_administrators`. It was a
role list that doubled as an authorization tier, and keeping the column would
leave a second, silent source of truth for something `/perm` now owns.

Deliberately a separate revision from the backfill, so a deployment can soak on
both systems and roll the application back without losing data. Downgrading
recreates the tables empty: the grants themselves live on as role assignments,
and reconstructing tier rows from them would be guesswork.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-08-14 12:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER = "global_administrators_bump_epoch"


def upgrade() -> None:
    """Remove the tier tables, their trigger, and the Trusted role column."""
    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON public.global_administrators")
    op.drop_table("global_administrators")
    op.drop_column("server_settings", "trusted_roles_ids")


def downgrade() -> None:
    """Recreate the tier storage, empty."""
    op.add_column("server_settings", sa.Column("trusted_roles_ids", sa.ARRAY(sa.BigInteger()), nullable=True))
    op.create_table(
        "global_administrators",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("granted_by_account_id", sa.Integer(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="global_administrators_account_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_account_id"],
            ["accounts.id"],
            name="global_administrators_granted_by_account_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("account_id"),
        comment="An active bot-wide administrator grant.",
    )
    op.execute(
        "CREATE TRIGGER global_administrators_bump_epoch "
        "AFTER INSERT OR DELETE OR UPDATE ON public.global_administrators "
        "FOR EACH STATEMENT EXECUTE FUNCTION public.bump_permission_epoch()"
    )
