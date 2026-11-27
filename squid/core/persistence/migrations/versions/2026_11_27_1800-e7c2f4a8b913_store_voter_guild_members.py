"""Store consented voters' guild roles, so ballots are weighed without asking Discord.

The bot fills the table from its member cache when each guild becomes available.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

from alembic import op

revision: str = "e7c2f4a8b913"
down_revision: str | None = "d58a3b9e1f02"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "voter_guild_members",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("discord_user_id", sa.BigInteger(), nullable=False),
        sa.Column("role_ids", ARRAY(sa.BigInteger()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("manage_server", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="When the bot last saw this membership; a complete guild sync forgets older rows.",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="voter_guild_members_account_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("guild_id", "account_id", name="voter_guild_members_pkey"),
        comment="A consented voter's roles in one guild, as the bot last saw them.",
    )
    op.create_index("voter_guild_members_discord_user_idx", "voter_guild_members", ["guild_id", "discord_user_id"])


def downgrade() -> None:
    op.drop_table("voter_guild_members")
