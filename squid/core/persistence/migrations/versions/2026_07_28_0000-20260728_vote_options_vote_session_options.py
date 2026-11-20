"""Add configurable vote-session options.

Revision ID: 20260728_vote_options
Revises: 20260728_baseline
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260728_vote_options"
down_revision: str | Sequence[str] | None = "20260728_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create vote options and backfill the legacy four reactions."""
    op.create_table(
        "vote_session_options",
        sa.Column("vote_session_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("choice", sa.Text(), nullable=False),
        sa.Column("multiplier", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("choice IN ('approve', 'deny')", name="vote_session_options_choice_check"),
        sa.CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="vote_session_options_multiplier_check",
        ),
        sa.CheckConstraint("position >= 0", name="vote_session_options_position_check"),
        sa.ForeignKeyConstraint(
            ["vote_session_id"],
            ["vote_sessions.id"],
            name="vote_session_options_vote_session_id_fkey",
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("vote_session_id", "emoji", name="vote_session_options_pkey"),
        sa.UniqueConstraint(
            "vote_session_id",
            "position",
            name="vote_session_options_vote_session_id_position_key",
        ),
        comment="Ordered reaction options and positive weight multipliers captured for each vote session.",
    )
    op.execute(
        """
        INSERT INTO public.vote_session_options (vote_session_id, emoji, choice, multiplier, position)
        SELECT vote_sessions.id, defaults.emoji, defaults.choice, 1.0, defaults.position
        FROM public.vote_sessions
        CROSS JOIN (
            VALUES
                ('👍', 'approve', 0),
                ('✅', 'approve', 1),
                ('👎', 'deny', 2),
                ('❌', 'deny', 3)
        ) AS defaults(emoji, choice, position)
        """
    )


def downgrade() -> None:
    """Remove configurable vote options."""
    op.drop_table("vote_session_options")
