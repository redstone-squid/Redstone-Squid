"""Reference server_settings from votes.guild_id.

Every vote is cast on a card in some guild, and `votes.guild_id` records which one. Settings rows
used to be created only when the bot saw itself join a guild, so a guild joined while the bot was
offline could have votes but no settings row. Create the missing rows first, with default
settings, then add the foreign key. `ON DELETE RESTRICT` matches `generic_vote_sessions.guild_id`;
settings rows are never deleted when the bot leaves a guild.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "b8e3d6f9a2c4"
down_revision: str | None = "a7d2c5e8f1b3"
branch_labels: str | None = None
depends_on: str | None = None

_GUILD_COMMENT = "The guild whose card the ballot was cast on, which decides the option's emoji alias."


def upgrade() -> None:
    op.execute("""
        INSERT INTO server_settings (server_id)
        SELECT DISTINCT guild_id FROM votes WHERE guild_id IS NOT NULL
        ON CONFLICT (server_id) DO NOTHING
    """)
    op.create_foreign_key(
        "votes_guild_id_fkey",
        "votes",
        "server_settings",
        ["guild_id"],
        ["server_id"],
        ondelete="RESTRICT",
    )
    op.alter_column("votes", "guild_id", existing_type=sa.BigInteger(), existing_nullable=True, comment=_GUILD_COMMENT)


def downgrade() -> None:
    op.alter_column(
        "votes",
        "guild_id",
        existing_type=sa.BigInteger(),
        existing_nullable=True,
        comment=None,
        existing_comment=_GUILD_COMMENT,
    )
    op.drop_constraint("votes_guild_id_fkey", "votes", type_="foreignkey")
