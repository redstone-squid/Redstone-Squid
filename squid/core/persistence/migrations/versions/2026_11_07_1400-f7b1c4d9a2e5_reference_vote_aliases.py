"""Reference exact vote option aliases with session-scoped foreign keys."""

import sqlalchemy as sa

from alembic import op

revision: str = "f7b1c4d9a2e5"
down_revision: str | None = "e6a0b3c8f1d4"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("vote_session_options", sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False))
    op.drop_constraint("vote_session_options_pkey", "vote_session_options", type_="primary")
    op.create_primary_key("vote_session_options_pkey", "vote_session_options", ["id"])
    op.alter_column(
        "vote_session_options",
        "guild_id",
        nullable=True,
        server_default=None,
        comment="Guild using this emoji alias, or None for an option shared across guilds.",
    )
    op.execute("UPDATE vote_session_options SET guild_id = NULL WHERE guild_id = 0")
    op.create_unique_constraint(
        "vote_session_options_alias_key",
        "vote_session_options",
        ["vote_session_id", "guild_id", "emoji"],
        postgresql_nulls_not_distinct=True,
    )
    op.create_unique_constraint(
        "vote_session_options_session_id_key", "vote_session_options", ["vote_session_id", "id"]
    )
    op.drop_constraint("vote_session_options_vote_session_id_position_key", "vote_session_options", type_="unique")
    op.create_unique_constraint(
        "vote_session_options_vote_session_id_position_key",
        "vote_session_options",
        ["vote_session_id", "guild_id", "position"],
        postgresql_nulls_not_distinct=True,
    )
    op.add_column("votes", sa.Column("option_alias_id", sa.BigInteger(), nullable=True))
    op.execute("""
        UPDATE votes v SET option_alias_id = (
            SELECT o.id FROM vote_session_options o
            WHERE o.vote_session_id = v.vote_session_id AND o.identifier = v.option_id AND o.emoji = v.emoji
              AND (o.guild_id = v.guild_id OR o.guild_id IS NULL)
            ORDER BY o.guild_id NULLS LAST LIMIT 1
        )
    """)
    op.execute("DELETE FROM votes WHERE option_alias_id IS NULL")
    op.alter_column("votes", "option_alias_id", nullable=False)
    op.create_foreign_key(
        "votes_option_alias_fkey",
        "votes",
        "vote_session_options",
        ["vote_session_id", "option_alias_id"],
        ["vote_session_id", "id"],
    )
    op.create_index("votes_option_alias_idx", "votes", ["vote_session_id", "option_alias_id"])
    op.alter_column("votes", "guild_id", nullable=True, server_default=None)
    op.execute("UPDATE votes SET guild_id = NULL WHERE guild_id = 0")
    op.drop_column("votes", "option_id")
    op.drop_column("votes", "emoji")


def downgrade() -> None:
    op.add_column("votes", sa.Column("option_id", sa.Text(), nullable=True))
    op.add_column("votes", sa.Column("emoji", sa.Text(), nullable=True))
    op.execute("""
        UPDATE votes v SET option_id = o.identifier, emoji = o.emoji
        FROM vote_session_options o WHERE o.id = v.option_alias_id
    """)
    op.alter_column("votes", "option_id", nullable=False)
    op.alter_column("votes", "emoji", nullable=False)
    op.drop_index("votes_option_alias_idx", table_name="votes")
    op.drop_constraint("votes_option_alias_fkey", "votes", type_="foreignkey")
    op.drop_column("votes", "option_alias_id")
    op.execute("UPDATE votes SET guild_id = 0 WHERE guild_id IS NULL")
    op.alter_column("votes", "guild_id", nullable=False, server_default=sa.text("0"))
    op.drop_constraint("vote_session_options_alias_key", "vote_session_options", type_="unique")
    op.drop_constraint("vote_session_options_session_id_key", "vote_session_options", type_="unique")
    op.drop_constraint("vote_session_options_vote_session_id_position_key", "vote_session_options", type_="unique")
    op.execute("UPDATE vote_session_options SET guild_id = 0 WHERE guild_id IS NULL")
    op.alter_column(
        "vote_session_options",
        "guild_id",
        nullable=False,
        server_default=sa.text("0"),
        comment="Guild using this emoji alias, or 0 for an option shared across guilds.",
    )
    op.drop_constraint("vote_session_options_pkey", "vote_session_options", type_="primary")
    op.drop_column("vote_session_options", "id")
    op.create_primary_key("vote_session_options_pkey", "vote_session_options", ["vote_session_id", "guild_id", "emoji"])
    op.create_unique_constraint(
        "vote_session_options_vote_session_id_position_key",
        "vote_session_options",
        ["vote_session_id", "guild_id", "position"],
    )
