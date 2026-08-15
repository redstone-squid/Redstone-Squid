"""Replace generic-poll threshold sentinels with NULL and free polls from a guild.

Generic polls never close on a score, but `pass_threshold`/`fail_threshold` were
`NOT NULL` with sign checks, so creating one had to store `32767`/`-32768` to get
past the schema. Those sentinels are indistinguishable from a real threshold once
read back, and every consumer had to know to ignore them for one kind.

`generic_vote_sessions.guild_id` was likewise `NOT NULL`, which made "create a poll"
and "publish it into a Discord guild" the same operation and left no way to create
one from the REST API.

Revision ID: b2c3d4e5f6a8
Revises: b1c2d3e4f5a7
Create Date: 2026-08-15 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_THRESHOLD_CHECK = (
    "CASE WHEN kind = 'generic'"
    " THEN pass_threshold IS NULL AND fail_threshold IS NULL"
    " ELSE pass_threshold > 0 AND fail_threshold < 0"
    " END"
)


def upgrade() -> None:
    """Null out generic sentinels and constrain thresholds by session kind."""
    op.drop_constraint("vote_sessions_pass_threshold_check", "vote_sessions", type_="check")
    op.drop_constraint("vote_sessions_fail_threshold_check", "vote_sessions", type_="check")
    op.alter_column("vote_sessions", "pass_threshold", existing_type=sa.SmallInteger(), nullable=True)
    op.alter_column("vote_sessions", "fail_threshold", existing_type=sa.SmallInteger(), nullable=True)
    op.execute(
        "UPDATE vote_sessions SET pass_threshold = NULL, fail_threshold = NULL WHERE kind = 'generic'",
    )
    op.create_check_constraint("vote_sessions_threshold_kind_check", "vote_sessions", _THRESHOLD_CHECK)
    op.create_check_constraint(
        "vote_sessions_kind_check", "vote_sessions", "kind = ANY (ARRAY['build', 'delete_log', 'generic'])"
    )
    op.create_check_constraint(
        "vote_sessions_status_check", "vote_sessions", "status = ANY (ARRAY['open', 'closed'])"
    )

    op.alter_column("generic_vote_sessions", "guild_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade() -> None:
    """Restore the sentinels and the guild requirement.

    Guild-less polls have no guild to invent, so they are removed rather than
    silently reassigned to an unrelated server.
    """
    op.execute("DELETE FROM vote_sessions WHERE id IN (SELECT vote_session_id FROM generic_vote_sessions WHERE guild_id IS NULL)")
    op.alter_column("generic_vote_sessions", "guild_id", existing_type=sa.BigInteger(), nullable=False)

    op.drop_constraint("vote_sessions_status_check", "vote_sessions", type_="check")
    op.drop_constraint("vote_sessions_kind_check", "vote_sessions", type_="check")
    op.drop_constraint("vote_sessions_threshold_kind_check", "vote_sessions", type_="check")
    op.execute(
        "UPDATE vote_sessions SET pass_threshold = 32767, fail_threshold = -32768"
        " WHERE pass_threshold IS NULL OR fail_threshold IS NULL"
    )
    op.alter_column("vote_sessions", "pass_threshold", existing_type=sa.SmallInteger(), nullable=False)
    op.alter_column("vote_sessions", "fail_threshold", existing_type=sa.SmallInteger(), nullable=False)
    op.create_check_constraint("vote_sessions_pass_threshold_check", "vote_sessions", "pass_threshold > 0")
    op.create_check_constraint("vote_sessions_fail_threshold_check", "vote_sessions", "fail_threshold < 0")
