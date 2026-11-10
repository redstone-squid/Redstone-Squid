"""Persist runtime behavior settings and administrator change history."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c7d0e5f8a1"
down_revision: str | None = "e1b6a9c4d7f0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("welcome_forward_chance", sa.Float(), nullable=False),
        sa.Column("welcome_enabled", sa.Boolean(), nullable=False),
        sa.Column("redstoner_enabled", sa.Boolean(), nullable=False),
        sa.Column("build_vote_pass_threshold", sa.SmallInteger(), nullable=False),
        sa.Column("build_vote_fail_threshold", sa.SmallInteger(), nullable=False),
        sa.Column("delete_vote_pass_threshold", sa.SmallInteger(), nullable=False),
        sa.Column("delete_vote_fail_threshold", sa.SmallInteger(), nullable=False),
        sa.Column("notification_retention_days", sa.Integer(), nullable=False),
        sa.Column("redstoner_starboard_author_id", sa.BigInteger(), nullable=False),
        sa.Column("redstoner_starboard_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("redstoner_role_id", sa.BigInteger(), nullable=False),
        sa.Column("redstoner_corner_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("redstoner_announcement_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("welcome_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("welcome_relay_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("version_tracker_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("build_log_channel_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.CheckConstraint("id = 1", name="runtime_settings_singleton"),
        sa.CheckConstraint("welcome_forward_chance BETWEEN 0 AND 1", name="runtime_settings_chance"),
        sa.CheckConstraint("notification_retention_days BETWEEN 1 AND 3650", name="runtime_settings_retention"),
        sa.CheckConstraint(
            "build_vote_pass_threshold > 0 AND build_vote_fail_threshold < 0 "
            "AND delete_vote_pass_threshold > 0 AND delete_vote_fail_threshold < 0",
            name="runtime_settings_thresholds",
        ),
    )
    op.create_table(
        "runtime_setting_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("actor_discord_id", sa.BigInteger(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("runtime_setting_changes")
    op.drop_table("runtime_settings")
