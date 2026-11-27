"""Let a Discord user vote through the bot without a consented account.

A ballot belongs to exactly one of an account or a Discord user ID. Ballots get a
surrogate key, since the voter is no longer one non-null column.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9d1c6e824"
down_revision: str | None = "e7c2f4a8b913"
branch_labels: str | None = None
depends_on: str | None = None

_VOTER_COMMENT = "The voter's Discord user ID when they voted through the bot without a consented account."
_EVENT_VOTER_COMMENT = "The voter's Discord user ID when the change came from someone without a consented account."


def upgrade() -> None:
    op.drop_constraint("votes_pkey", "votes", type_="primary")
    op.add_column("votes", sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False))
    op.create_primary_key("votes_pkey", "votes", ["id"])
    op.alter_column("votes", "account_id", nullable=True)
    op.add_column("votes", sa.Column("discord_user_id", sa.BigInteger(), nullable=True, comment=_VOTER_COMMENT))
    op.create_check_constraint("votes_one_voter_check", "votes", "num_nonnulls(account_id, discord_user_id) = 1")
    op.create_index(
        "votes_session_account_key",
        "votes",
        ["vote_session_id", "account_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )
    op.create_index(
        "votes_session_discord_user_key",
        "votes",
        ["vote_session_id", "discord_user_id"],
        unique=True,
        postgresql_where=sa.text("discord_user_id IS NOT NULL"),
    )
    op.create_table_comment("votes", "One voter's selected option and the positive weight it was cast with.")

    op.alter_column("vote_ballot_events", "account_id", nullable=True)
    op.add_column(
        "vote_ballot_events",
        sa.Column("discord_user_id", sa.BigInteger(), nullable=True, comment=_EVENT_VOTER_COMMENT),
    )
    op.create_check_constraint(
        "vote_ballot_events_one_voter_check", "vote_ballot_events", "num_nonnulls(account_id, discord_user_id) = 1"
    )


def downgrade() -> None:
    # Ballots from Discord users without accounts have nowhere to go.
    op.execute("DELETE FROM vote_ballot_events WHERE account_id IS NULL")
    op.drop_constraint("vote_ballot_events_one_voter_check", "vote_ballot_events", type_="check")
    op.drop_column("vote_ballot_events", "discord_user_id")
    op.alter_column("vote_ballot_events", "account_id", nullable=False)

    op.execute("DELETE FROM votes WHERE account_id IS NULL")
    op.drop_index("votes_session_discord_user_key", table_name="votes")
    op.drop_index("votes_session_account_key", table_name="votes")
    op.drop_constraint("votes_one_voter_check", "votes", type_="check")
    op.drop_column("votes", "discord_user_id")
    op.alter_column("votes", "account_id", nullable=False)
    op.drop_constraint("votes_pkey", "votes", type_="primary")
    op.drop_column("votes", "id")
    op.create_primary_key("votes_pkey", "votes", ["vote_session_id", "account_id"])
    op.create_table_comment("votes", "One account's selected option and the positive weight it was cast with.")
