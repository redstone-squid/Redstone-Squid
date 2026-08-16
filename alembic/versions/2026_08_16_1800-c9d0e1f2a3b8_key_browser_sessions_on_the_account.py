"""Key browser sessions on the account, and mint OAuth state per provider

`web_sessions.discord_id` was `NOT NULL`, so a browser session could not exist without a
Discord identity -- a second web login was unimplementable without touching the schema.
The session already carries the `account_id` that everything downstream reads, and the
identity it was established through lives in `account_identities`, where a second
provider's would too.

`oauth_states.provider` is a security fix rather than bookkeeping. Without it, a state
minted for provider A is redeemable at provider B's callback, which is the IdP mix-up
class; the callback now refuses a state whose provider does not match the slug in the
URL. It is why the column is `NOT NULL` and why it lands with the templated route.

Revision ID: c9d0e1f2a3b8
Revises: b8c9d0e1f2a7
Create Date: 2026-08-16 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b8"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("web_sessions", "discord_id")
    # Defaulted for the rows in flight, then dropped: every existing state was minted by
    # the only adapter that existed, and nothing should default a provider from here on.
    op.add_column(
        "oauth_states",
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'discord'")),
    )
    op.alter_column("oauth_states", "provider", server_default=None)


def downgrade() -> None:
    """Re-add the session snowflake nullable, and leave it empty.

    Backfilling it from `account_identities` would be pointless: the column existed to
    record which identity established a session, and an account that has since linked or
    unlinked Discord would be assigned an identity it never logged in with. Old code
    reads the column but nothing authorizes on it.
    """
    op.drop_column("oauth_states", "provider")
    op.add_column("web_sessions", sa.Column("discord_id", sa.BigInteger(), nullable=True))
