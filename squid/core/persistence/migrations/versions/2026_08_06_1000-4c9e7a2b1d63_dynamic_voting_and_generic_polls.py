"""Add dynamic voting configuration and generic polls.

Revision ID: 4c9e7a2b1d63
Revises: d9f6a8b2c4e1
Create Date: 2026-08-06 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4c9e7a2b1d63"
down_revision: str | Sequence[str] | None = "d9f6a8b2c4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Normalize selections, presets, role weights, and generic metadata."""
    op.add_column("vote_session_options", sa.Column("identifier", sa.Text(), nullable=True))
    op.add_column(
        "vote_session_options", sa.Column("guild_id", sa.BigInteger(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column("vote_session_options", sa.Column("label", sa.Text(), nullable=True))
    op.execute("UPDATE vote_session_options SET identifier = choice WHERE identifier IS NULL")
    op.alter_column("vote_session_options", "identifier", nullable=False)
    op.drop_constraint("vote_session_options_pkey", "vote_session_options", type_="primary")
    op.drop_constraint("vote_session_options_vote_session_id_position_key", "vote_session_options", type_="unique")
    op.create_primary_key("vote_session_options_pkey", "vote_session_options", ["vote_session_id", "guild_id", "emoji"])
    op.create_unique_constraint(
        "vote_session_options_vote_session_id_position_key",
        "vote_session_options",
        ["vote_session_id", "guild_id", "position"],
    )
    op.drop_constraint("vote_session_options_choice_check", "vote_session_options", type_="check")
    op.create_check_constraint(
        "vote_session_options_choice_check", "vote_session_options", "choice IN ('approve', 'deny', 'generic')"
    )

    op.add_column("votes", sa.Column("guild_id", sa.BigInteger(), server_default=sa.text("0"), nullable=False))
    op.add_column("votes", sa.Column("option_id", sa.Text(), nullable=True))
    op.add_column("votes", sa.Column("emoji", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE votes AS vote
        SET option_id = (
            SELECT choice
            FROM vote_session_options
            WHERE vote_session_id = vote.vote_session_id
              AND choice = CASE WHEN coalesce(vote.weight, 1) < 0 THEN 'deny' ELSE 'approve' END
            ORDER BY position
            LIMIT 1
        ), emoji = (
            SELECT emoji
            FROM vote_session_options
            WHERE vote_session_id = vote.vote_session_id
              AND choice = CASE WHEN coalesce(vote.weight, 1) < 0 THEN 'deny' ELSE 'approve' END
            ORDER BY position
            LIMIT 1
        ), weight = coalesce(abs(vote.weight), 1)
        WHERE EXISTS (
            SELECT choice, emoji
            FROM vote_session_options
            WHERE vote_session_id = vote.vote_session_id
              AND choice = CASE WHEN coalesce(vote.weight, 1) < 0 THEN 'deny' ELSE 'approve' END
        )
        """
    )
    op.alter_column("votes", "option_id", nullable=False)
    op.alter_column("votes", "emoji", nullable=False)
    op.alter_column("votes", "weight", nullable=False)
    op.create_check_constraint(
        "votes_weight_check",
        "votes",
        "weight > 0 AND weight != 'Infinity'::double precision AND weight != 'NaN'::double precision",
    )

    op.create_table(
        "generic_vote_sessions",
        sa.Column("vote_session_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "visibility IN ('anonymous_live', 'visible_live', 'anonymous_hidden')",
            name="generic_vote_sessions_visibility_check",
        ),
        sa.ForeignKeyConstraint(["vote_session_id"], ["vote_sessions.id"], onupdate="CASCADE", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guild_id"], ["server_settings.server_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("vote_session_id"),
        comment="Metadata for a user-created generic poll.",
    )
    op.create_index("generic_vote_sessions_deadline_idx", "generic_vote_sessions", ["deadline"])
    op.create_table(
        "guild_vote_emojis",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("choice", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("kind IN ('build', 'delete_log', 'generic')"),
        sa.CheckConstraint("choice IN ('approve', 'deny', 'generic')"),
        sa.ForeignKeyConstraint(["guild_id"], ["server_settings.server_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "kind", "emoji"),
        sa.UniqueConstraint("guild_id", "kind", "position", name="guild_vote_emojis_position_key"),
        comment="One ordered emoji in a guild/session-kind preset.",
    )
    op.create_table(
        "guild_vote_role_weights",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("multiplier", sa.Float(), nullable=False),
        sa.CheckConstraint("kind IN ('build', 'delete_log', 'generic')"),
        sa.CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="guild_vote_role_weights_multiplier_check",
        ),
        sa.ForeignKeyConstraint(["guild_id"], ["server_settings.server_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "kind", "role_id"),
        comment="A role multiplier scoped to one guild and session kind.",
    )


def downgrade() -> None:
    """Remove generic sessions and restore signed legacy votes/options."""
    op.execute("DELETE FROM vote_sessions WHERE kind = 'generic'")
    op.drop_table("guild_vote_role_weights")
    op.drop_table("guild_vote_emojis")
    op.drop_index("generic_vote_sessions_deadline_idx", table_name="generic_vote_sessions")
    op.drop_table("generic_vote_sessions")

    op.execute(
        """
        UPDATE votes AS vote
        SET weight = CASE WHEN option.choice = 'deny' THEN -vote.weight ELSE vote.weight END
        FROM vote_session_options AS option
        WHERE option.vote_session_id = vote.vote_session_id
          AND option.identifier = vote.option_id
          AND option.guild_id IN (vote.guild_id, 0)
        """
    )
    op.drop_constraint("votes_weight_check", "votes", type_="check")
    op.drop_column("votes", "emoji")
    op.drop_column("votes", "option_id")
    op.drop_column("votes", "guild_id")

    op.execute(
        """
        DELETE FROM vote_session_options AS option
        WHERE option.guild_id != (
            SELECT min(preferred.guild_id)
            FROM vote_session_options AS preferred
            WHERE preferred.vote_session_id = option.vote_session_id
        )
        """
    )
    op.drop_constraint("vote_session_options_choice_check", "vote_session_options", type_="check")
    op.create_check_constraint(
        "vote_session_options_choice_check", "vote_session_options", "choice IN ('approve', 'deny')"
    )
    op.drop_constraint("vote_session_options_vote_session_id_position_key", "vote_session_options", type_="unique")
    op.drop_constraint("vote_session_options_pkey", "vote_session_options", type_="primary")
    op.create_primary_key("vote_session_options_pkey", "vote_session_options", ["vote_session_id", "emoji"])
    op.create_unique_constraint(
        "vote_session_options_vote_session_id_position_key", "vote_session_options", ["vote_session_id", "position"]
    )
    op.drop_column("vote_session_options", "label")
    op.drop_column("vote_session_options", "guild_id")
    op.drop_column("vote_session_options", "identifier")
