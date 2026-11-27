"""Detach network polls from the guild that created them.

A network poll was weighed by its creating guild's roles. Now no guild weighs or
manages a poll every guild votes in, so its guild is cleared. Ballots already cast
keep the weight they were cast with. The node for creating one moves under the
global vote.network namespace, and stored grants and role patterns follow it.
"""

from alembic import op

revision: str = "a4e8b2d7c615"
down_revision: str | None = "f3a9d1c6e824"
branch_labels: str | None = None
depends_on: str | None = None


_RENAMED = (("vote.poll.network_create", "vote.network.create"),)


def _rename(pairs: tuple[tuple[str, str], ...]) -> None:
    for before, after in pairs:
        for table in ("permission_grants", "permission_role_patterns"):
            op.execute(f"UPDATE {table} SET pattern = '{after}' WHERE pattern = '{before}'")


def upgrade() -> None:
    _rename(_RENAMED)
    op.drop_constraint("generic_vote_sessions_network_guild_check", "generic_vote_sessions", type_="check")
    op.execute("UPDATE generic_vote_sessions SET guild_id = NULL WHERE scope = 'network'")
    op.create_check_constraint(
        "generic_vote_sessions_network_guild_check", "generic_vote_sessions", "scope = 'guild' OR guild_id IS NULL"
    )
    op.alter_column(
        "generic_vote_sessions",
        "guild_id",
        comment="The guild a server poll belongs to, whose roles weigh its ballots; network polls have none.",
    )


def downgrade() -> None:
    _rename(tuple((after, before) for before, after in _RENAMED))
    op.drop_constraint("generic_vote_sessions_network_guild_check", "generic_vote_sessions", type_="check")
    # The creating guild was not kept; the guild of the poll's first card stands in for it.
    op.execute("""
        UPDATE generic_vote_sessions g SET guild_id = (
            SELECT m.guild_id FROM discord_posts p JOIN messages m ON m.id = p.message_id
            WHERE p.resource_kind = 'vote_session' AND p.resource_key = g.vote_session_id::text
            ORDER BY p.message_id LIMIT 1
        )
        WHERE g.scope = 'network'
    """)
    op.execute("""
        DELETE FROM vote_sessions WHERE id IN (
            SELECT vote_session_id FROM generic_vote_sessions WHERE scope = 'network' AND guild_id IS NULL
        )
    """)
    op.create_check_constraint(
        "generic_vote_sessions_network_guild_check", "generic_vote_sessions", "scope = 'guild' OR guild_id IS NOT NULL"
    )
