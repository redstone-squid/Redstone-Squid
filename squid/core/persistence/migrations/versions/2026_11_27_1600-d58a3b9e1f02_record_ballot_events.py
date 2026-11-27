"""Record every ballot change as an append-only event.

Existing ballots get one cast event each. Their cast time was never stored, so the
backfill stamps them with the migration time.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "d58a3b9e1f02"
down_revision: str | None = "c41f7e2a9d58"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "vote_ballot_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("vote_session_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column(
            "option_identifier",
            sa.Text(),
            nullable=True,
            comment="The option the ballot selects after a cast, or None after a clear or void.",
        ),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column(
            "guild_id",
            sa.BigInteger(),
            nullable=True,
            comment="The guild whose card the change came from, or None for a change made without Discord.",
        ),
        sa.Column("emoji", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("change IN ('cast', 'clear', 'void')", name="vote_ballot_events_change_check"),
        sa.CheckConstraint(
            "(change = 'cast') = (option_identifier IS NOT NULL AND weight IS NOT NULL)",
            name="vote_ballot_events_selection_check",
        ),
        sa.ForeignKeyConstraint(
            ["vote_session_id"],
            ["vote_sessions.id"],
            name="vote_ballot_events_vote_session_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="vote_ballot_events_account_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["vote_session_id", "option_identifier"],
            ["vote_session_options.vote_session_id", "vote_session_options.identifier"],
            name="vote_ballot_events_option_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="vote_ballot_events_pkey"),
        comment="One change to an account's ballot, kept after the ballot itself changes again.",
    )
    op.create_index("vote_ballot_events_session_idx", "vote_ballot_events", ["vote_session_id", "id"])
    op.create_index("vote_ballot_events_account_idx", "vote_ballot_events", ["account_id"])
    op.execute("""
        INSERT INTO vote_ballot_events (vote_session_id, account_id, change, option_identifier, weight, guild_id, emoji)
        SELECT vote_session_id, account_id, 'cast', option_identifier, weight, guild_id, emoji
        FROM votes ORDER BY vote_session_id, account_id
    """)


def downgrade() -> None:
    op.drop_table("vote_ballot_events")
