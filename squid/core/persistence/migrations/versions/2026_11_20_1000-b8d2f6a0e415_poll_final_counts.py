"""Persist generic poll final counts and staff review decisions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "b8d2f6a0e415"
down_revision = "c9f3a7e1b5d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("vote_sessions_status_check", "vote_sessions", type_="check")
    op.create_check_constraint(
        "vote_sessions_status_check", "vote_sessions", "status IN ('open','closed','counting','review','void')"
    )
    for column in ("voting_ended_at", "retry_at"):
        op.add_column("generic_vote_sessions", sa.Column(column, sa.DateTime(timezone=True)))
    for column in ("count_revision", "count_attempts"):
        op.add_column("generic_vote_sessions", sa.Column(column, sa.Integer(), nullable=False, server_default="0"))
    for column in ("unreadable_cards", "review_history"):
        op.add_column("generic_vote_sessions", sa.Column(column, JSONB(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_constraint("vote_sessions_status_check", "vote_sessions", type_="check")
    op.create_check_constraint("vote_sessions_status_check", "vote_sessions", "status IN ('open','closed')")
    for column in (
        "voting_ended_at",
        "retry_at",
        "count_revision",
        "count_attempts",
        "unreadable_cards",
        "review_history",
    ):
        op.drop_column("generic_vote_sessions", column)
