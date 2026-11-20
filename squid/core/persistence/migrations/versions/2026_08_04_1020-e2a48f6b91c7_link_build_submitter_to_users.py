"""link build submitter to users

Revision ID: e2a48f6b91c7
Revises: d5b93c17e284
Create Date: 2026-08-04 10:20:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2a48f6b91c7"
down_revision: str | Sequence[str] | None = "d5b93c17e284"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Replace the bare submitter snowflake with a foreign key onto `users`.

    A submitter-only row stores nothing beyond the Discord ID the column
    already held, so it needs no consent receipt; the receipt covers the
    Minecraft link, which such a row does not have.
    """
    op.execute(
        """
        INSERT INTO public.users (discord_id)
        SELECT DISTINCT b.submitter_id
        FROM public.builds b
        WHERE NOT EXISTS (SELECT 1 FROM public.users u WHERE u.discord_id = b.submitter_id)
        """
    )
    op.add_column("builds", sa.Column("submitter_user_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE public.builds b
        SET submitter_user_id = u.id
        FROM public.users u
        WHERE u.discord_id = b.submitter_id
        """
    )
    op.alter_column("builds", "submitter_user_id", nullable=False)
    op.create_foreign_key(
        "builds_submitter_user_id_fkey", "builds", "users", ["submitter_user_id"], ["id"], ondelete="RESTRICT"
    )
    op.drop_column("builds", "submitter_id")


def downgrade() -> None:
    """Restore the submitter Discord snowflake column."""
    op.add_column("builds", sa.Column("submitter_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE public.builds b
        SET submitter_id = u.discord_id
        FROM public.users u
        WHERE u.id = b.submitter_user_id
        """
    )
    op.execute("DELETE FROM public.builds WHERE submitter_id IS NULL")
    op.alter_column("builds", "submitter_id", nullable=False)
    op.drop_constraint("builds_submitter_user_id_fkey", "builds", type_="foreignkey")
    op.drop_column("builds", "submitter_user_id")
