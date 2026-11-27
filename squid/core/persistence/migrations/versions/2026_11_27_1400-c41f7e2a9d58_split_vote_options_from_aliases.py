"""Split vote session options from the emoji aliases that select them.

Each option row used to be one emoji alias carrying its own choice, label, and
multiplier, so two aliases of one option could weigh a ballot differently. Options
now have one row per stable identifier, aliases only map an emoji to an option, and
ballots reference the option and keep the emoji they were cast with.

Aliases of one option are collapsed to the option's first row, preferring the
shared alias over guild-scoped ones.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "c41f7e2a9d58"
down_revision: str | None = "b3e7c1d94f20"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "vote_session_option_aliases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("vote_session_id", sa.BigInteger(), nullable=False),
        sa.Column("option_identifier", sa.Text(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("position >= 0", name="vote_session_option_aliases_position_check"),
    )
    op.execute("""
        INSERT INTO vote_session_option_aliases (vote_session_id, option_identifier, guild_id, emoji, position)
        SELECT vote_session_id, identifier, guild_id, emoji, position FROM vote_session_options ORDER BY id
    """)

    op.add_column("votes", sa.Column("option_identifier", sa.Text(), nullable=True))
    op.add_column("votes", sa.Column("emoji", sa.Text(), nullable=True))
    op.execute("""
        UPDATE votes v SET option_identifier = o.identifier, emoji = o.emoji
        FROM vote_session_options o WHERE o.id = v.option_alias_id
    """)
    op.alter_column("votes", "option_identifier", nullable=False)
    op.drop_index("votes_option_alias_idx", table_name="votes")
    op.drop_constraint("votes_option_alias_fkey", "votes", type_="foreignkey")
    op.drop_column("votes", "option_alias_id")

    op.execute("""
        DELETE FROM vote_session_options o USING (
            SELECT id, row_number() OVER (
                PARTITION BY vote_session_id, identifier ORDER BY guild_id NULLS FIRST, position, id
            ) AS rank
            FROM vote_session_options
        ) ranked
        WHERE ranked.id = o.id AND ranked.rank > 1
    """)
    for name in (
        "vote_session_options_alias_key",
        "vote_session_options_session_id_key",
        "vote_session_options_vote_session_id_position_key",
    ):
        op.drop_constraint(name, "vote_session_options", type_="unique")
    op.drop_constraint("vote_session_options_pkey", "vote_session_options", type_="primary")
    op.drop_column("vote_session_options", "id")
    op.drop_column("vote_session_options", "guild_id")
    op.drop_column("vote_session_options", "emoji")
    op.execute("""
        UPDATE vote_session_options o SET position = ranked.position - 1 FROM (
            SELECT vote_session_id, identifier,
                   row_number() OVER (PARTITION BY vote_session_id ORDER BY position, identifier) AS position
            FROM vote_session_options
        ) ranked
        WHERE ranked.vote_session_id = o.vote_session_id AND ranked.identifier = o.identifier
    """)
    op.create_primary_key("vote_session_options_pkey", "vote_session_options", ["vote_session_id", "identifier"])
    op.create_table_comment(
        "vote_session_options", "Ordered options and positive weight multipliers captured for each vote session."
    )

    op.create_foreign_key(
        "vote_session_option_aliases_option_fkey",
        "vote_session_option_aliases",
        "vote_session_options",
        ["vote_session_id", "option_identifier"],
        ["vote_session_id", "identifier"],
        ondelete="CASCADE",
        onupdate="CASCADE",
    )
    for name, columns in (
        ("vote_session_option_aliases_emoji_key", ["vote_session_id", "guild_id", "emoji"]),
        ("vote_session_option_aliases_position_key", ["vote_session_id", "guild_id", "position"]),
    ):
        op.create_unique_constraint(name, "vote_session_option_aliases", columns, postgresql_nulls_not_distinct=True)
    op.create_foreign_key(
        "votes_option_fkey",
        "votes",
        "vote_session_options",
        ["vote_session_id", "option_identifier"],
        ["vote_session_id", "identifier"],
    )
    op.create_table_comment(
        "vote_session_option_aliases",
        "An emoji that selects a session option on one guild's cards, or every guild's when guild_id is None.",
    )
    op.create_table_comment("votes", "One account's selected option and the positive weight it was cast with.")
    for column, comment in (
        ("guild_id", "The guild whose card the ballot was cast on, or None for a ballot cast without Discord."),
        ("emoji", "The alias the ballot was cast with, which a public card keeps as the voter's reaction."),
        (
            "weight",
            "Positive magnitude fixed at cast; the option's choice determines the sign used in threshold scoring.",
        ),
    ):
        op.alter_column("votes", column, comment=comment)


def downgrade() -> None:
    op.drop_constraint("votes_option_fkey", "votes", type_="foreignkey")
    op.drop_constraint("vote_session_option_aliases_option_fkey", "vote_session_option_aliases", type_="foreignkey")
    op.drop_constraint("vote_session_options_pkey", "vote_session_options", type_="primary")
    op.rename_table("vote_session_options", "vote_session_options_collapsed")
    op.create_table(
        "vote_session_options",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "vote_session_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "vote_sessions.id",
                name="vote_session_options_vote_session_id_fkey",
                ondelete="CASCADE",
                onupdate="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("choice", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("multiplier", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="vote_session_options_pkey"),
    )
    op.execute("""
        INSERT INTO vote_session_options
            (vote_session_id, identifier, guild_id, emoji, choice, label, multiplier, position)
        SELECT a.vote_session_id, a.option_identifier, a.guild_id, a.emoji, o.choice, o.label, o.multiplier, a.position
        FROM vote_session_option_aliases a
        JOIN vote_session_options_collapsed o
          ON o.vote_session_id = a.vote_session_id AND o.identifier = a.option_identifier
        ORDER BY a.id
    """)
    op.drop_table("vote_session_option_aliases")
    op.drop_table("vote_session_options_collapsed")
    op.create_check_constraint(
        "vote_session_options_choice_check", "vote_session_options", "choice IN ('approve', 'deny', 'generic')"
    )
    op.create_check_constraint(
        "vote_session_options_multiplier_check",
        "vote_session_options",
        "multiplier > 0 AND multiplier != 'Infinity'::double precision AND multiplier != 'NaN'::double precision",
    )
    op.create_check_constraint("vote_session_options_position_check", "vote_session_options", "position >= 0")
    for name, columns in (
        ("vote_session_options_alias_key", ["vote_session_id", "guild_id", "emoji"]),
        ("vote_session_options_vote_session_id_position_key", ["vote_session_id", "guild_id", "position"]),
    ):
        op.create_unique_constraint(name, "vote_session_options", columns, postgresql_nulls_not_distinct=True)
    op.create_unique_constraint(
        "vote_session_options_session_id_key", "vote_session_options", ["vote_session_id", "id"]
    )

    op.add_column("votes", sa.Column("option_alias_id", sa.BigInteger(), nullable=True))
    # Ballots cast without Discord have no emoji; they take the option's first alias.
    op.execute("""
        UPDATE votes v SET option_alias_id = (
            SELECT o.id FROM vote_session_options o
            WHERE o.vote_session_id = v.vote_session_id AND o.identifier = v.option_identifier
            ORDER BY (o.emoji = v.emoji) DESC NULLS LAST, (o.guild_id = v.guild_id) DESC NULLS LAST, o.position
            LIMIT 1
        )
    """)
    op.execute("DELETE FROM votes WHERE option_alias_id IS NULL")
    op.alter_column("votes", "option_alias_id", nullable=False)
    op.drop_column("votes", "emoji")
    op.drop_column("votes", "option_identifier")
    op.create_foreign_key(
        "votes_option_alias_fkey",
        "votes",
        "vote_session_options",
        ["vote_session_id", "option_alias_id"],
        ["vote_session_id", "id"],
    )
    op.create_index("votes_option_alias_idx", "votes", ["vote_session_id", "option_alias_id"])
