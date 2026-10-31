"""Drop the denormalized voter snowflake from votes

Every ballot already carries the `account_id` it belongs to, under a foreign key to
`accounts`. `votes.discord_id` was a second copy of the same voter, reachable through
`account_identities`, and the only thing it bought was letting the Discord transport skip
a join -- at the cost of making a ballot unwritable by anyone without a Discord identity.

`VoteActor.discord_id` deliberately survives in the application: that one is a live
guild-membership fact consumed for role weighting, not a stored copy of an identity.

Revision ID: e5f6a7b8c9d4
Revises: d4e5f6a7b8c3
Create Date: 2026-08-16 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d4"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("votes", "discord_id")


def downgrade() -> None:
    """Re-add the column and refill it from the identity it was a copy of.

    A voter with no Discord identity has no snowflake to restore, so the column comes
    back nullable rather than pretending one exists.
    """
    op.add_column("votes", sa.Column("discord_id", sa.BigInteger(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE votes SET discord_id = identity.subject::bigint "
            "FROM account_identities AS identity "
            "WHERE identity.account_id = votes.account_id AND identity.provider = 'discord'"
        )
    )
