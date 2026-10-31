"""Scope generic polls to a guild or the whole network.

Revision ID: c5b1e4a7d2f8
Revises: b4a0d3f6c8e2
Create Date: 2026-08-18 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5b1e4a7d2f8"
down_revision: str | None = "b4a0d3f6c8e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generic_vote_sessions",
        sa.Column(
            "scope",
            sa.Text(),
            nullable=False,
            server_default="guild",
            comment="Whether the poll is carded only in its own guild or in every vote channel.",
        ),
    )
    op.create_check_constraint(
        "generic_vote_sessions_scope_check",
        "generic_vote_sessions",
        "scope IN ('guild', 'network')",
    )
    # A network poll is carded in guilds that do not own it, so it needs an owner
    # to weigh its ballots. A guild poll may still be drafted without one.
    op.create_check_constraint(
        "generic_vote_sessions_network_guild_check",
        "generic_vote_sessions",
        "scope = 'guild' OR guild_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("generic_vote_sessions_network_guild_check", "generic_vote_sessions", type_="check")
    op.drop_constraint("generic_vote_sessions_scope_check", "generic_vote_sessions", type_="check")
    op.drop_column("generic_vote_sessions", "scope")
