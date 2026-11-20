"""Add weighted starboards.

Revision ID: 7f2c9d4e6a81
Revises: e1a7c3d9f5b2
Create Date: 2026-08-08 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7f2c9d4e6a81"
down_revision: str | Sequence[str] | None = "e1a7c3d9f5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create starboard configuration, vote, origin, and entry storage."""
    op.create_table(
        "starboards",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("required", sa.Float(), server_default=sa.text("3.0"), nullable=False),
        sa.Column("required_remove", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("self_vote", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("allow_bots", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("require_image", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("min_age_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_age_seconds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("autoreact_upvote", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("autoreact_downvote", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("remove_invalid_reactions", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("link_edits", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("link_deletes", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_emoji", sa.Text(), server_default=sa.text("'⭐'"), nullable=False),
        sa.Column("colour", sa.BigInteger(), server_default=sa.text("4415105"), nullable=False),
        sa.Column("jump_to_message", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("attachments_list", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("replied_to", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ping_author", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "min_age_seconds >= 0 AND max_age_seconds >= 0 "
            "AND (max_age_seconds = 0 OR min_age_seconds <= max_age_seconds)",
            name="starboards_age_check",
        ),
        sa.CheckConstraint("colour BETWEEN 0 AND 16777215", name="starboards_colour_check"),
        sa.CheckConstraint("btrim(name) != ''", name="starboards_name_check"),
        sa.CheckConstraint(
            "required > required_remove "
            "AND required != 'Infinity'::double precision "
            "AND required != '-Infinity'::double precision "
            "AND required != 'NaN'::double precision "
            "AND required_remove != 'Infinity'::double precision "
            "AND required_remove != '-Infinity'::double precision "
            "AND required_remove != 'NaN'::double precision",
            name="starboards_thresholds_check",
        ),
        sa.ForeignKeyConstraint(["guild_id"], ["server_settings.server_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="A named weighted-message board owned by one Discord guild.",
    )
    op.create_index("starboards_guild_name_key", "starboards", ["guild_id", sa.text("lower(name)")], unique=True)

    op.create_table(
        "starboard_emojis",
        sa.Column("starboard_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("multiplier", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("direction IN ('up', 'down')", name="starboard_emojis_direction_check"),
        sa.CheckConstraint("btrim(emoji) != ''", name="starboard_emojis_emoji_check"),
        sa.CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="starboard_emojis_multiplier_check",
        ),
        sa.CheckConstraint("position >= 0", name="starboard_emojis_position_check"),
        sa.ForeignKeyConstraint(["starboard_id"], ["starboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("starboard_id", "emoji"),
        comment="An ordered upvote or downvote emoji for a starboard.",
    )
    op.create_index("starboard_emojis_position_key", "starboard_emojis", ["starboard_id", "position"], unique=True)

    op.create_table(
        "starboard_sources",
        sa.Column("starboard_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["server_settings.server_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["starboard_id"], ["starboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("starboard_id", "guild_id", "channel_id"),
        comment="A guild or channel whose messages feed a starboard.",
    )

    op.create_table(
        "starboard_origin_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("author_is_bot", sa.Boolean(), nullable=False),
        sa.Column("is_nsfw", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("has_image", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["server_settings.server_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="A source message that has been evaluated by at least one starboard.",
    )

    op.create_table(
        "starboard_votes",
        sa.Column("starboard_id", sa.BigInteger(), nullable=False),
        sa.Column("origin_message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("target_author_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("direction IN ('up', 'down')", name="starboard_votes_direction_check"),
        sa.CheckConstraint(
            "weight > 0 AND weight != 'Infinity'::double precision AND weight != 'NaN'::double precision",
            name="starboard_votes_weight_check",
        ),
        sa.ForeignKeyConstraint(["origin_message_id"], ["starboard_origin_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["starboard_id"], ["starboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("starboard_id", "origin_message_id", "user_id"),
        comment="One member's current weighted reaction to one message on one starboard.",
    )
    op.create_index("starboard_votes_origin_message_idx", "starboard_votes", ["origin_message_id"])
    op.create_index(
        "starboard_votes_target_author_created_idx",
        "starboard_votes",
        ["starboard_id", "target_author_id", "created_at"],
    )

    op.create_table(
        "starboard_entries",
        sa.Column("starboard_id", sa.BigInteger(), nullable=False),
        sa.Column("origin_message_id", sa.BigInteger(), nullable=False),
        sa.Column("posted_message_id", sa.BigInteger(), nullable=True),
        sa.Column("posted_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("score", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("raw_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_rendered_score", sa.Float(), nullable=True),
        sa.Column("first_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["origin_message_id"], ["starboard_origin_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["starboard_id"], ["starboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("starboard_id", "origin_message_id"),
        comment="The materialized-post state for one source message on one starboard.",
    )
    op.create_index(
        "starboard_entries_posted_message_key",
        "starboard_entries",
        ["posted_message_id"],
        unique=True,
        postgresql_where=sa.text("posted_message_id IS NOT NULL"),
    )
    op.create_index("starboard_entries_score_idx", "starboard_entries", ["starboard_id", sa.text("score DESC")])

    op.create_table(
        "starboard_role_multipliers",
        sa.Column("starboard_id", sa.BigInteger(), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("multiplier", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
            name="starboard_role_multipliers_multiplier_check",
        ),
        sa.ForeignKeyConstraint(["starboard_id"], ["starboards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("starboard_id", "role_id"),
        comment="A role multiplier scoped to one starboard.",
    )


def downgrade() -> None:
    """Remove starboard storage."""
    op.drop_table("starboard_role_multipliers")
    op.drop_index("starboard_entries_score_idx", table_name="starboard_entries")
    op.drop_index("starboard_entries_posted_message_key", table_name="starboard_entries")
    op.drop_table("starboard_entries")
    op.drop_index("starboard_votes_target_author_created_idx", table_name="starboard_votes")
    op.drop_index("starboard_votes_origin_message_idx", table_name="starboard_votes")
    op.drop_table("starboard_votes")
    op.drop_table("starboard_origin_messages")
    op.drop_table("starboard_sources")
    op.drop_index("starboard_emojis_position_key", table_name="starboard_emojis")
    op.drop_table("starboard_emojis")
    op.drop_index("starboards_guild_name_key", table_name="starboards")
    op.drop_table("starboards")
