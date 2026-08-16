"""Gate staff notifications on a role, and drop the delivery snowflake

Two questions hid behind one config key, `SQUID_NOTIFICATION_STAFF_DISCORD_IDS`.

*May this caller read staff inbox items* is per-caller, and now resolves
`build.submission.view_pending` in the route -- staff notifications are *about* pending
submissions. That is also credential-bounded, so a leaked API key without the node cannot
read staff items, which an allowlist keyed on a snowflake could not express.

*Whom do we notify* is a set query over every account and keeps its `global-admin` role
subquery, minus the allowlist half.

The allowlist existed because the bot owner is in the audience only through
`Subject.is_bot_owner`, which short-circuits in code and is derived from
`bot.is_owner(user)` -- it is never a database row, so a set query cannot see it. This
seeds the owner a real `global-admin` assignment so the audience is unchanged. The seed
is guarded: it no-ops when no account holds that Discord identity, and the owner can
otherwise use `/role assign`.

`notification_deliveries.discord_id` goes with it. The DM address is read at claim time
from `account_identities` -- a join the write path already made to decide whether to
enqueue at all -- so unlinking Discord now suppresses a pending DM, which is the correct
reading of an unlink.

Revision ID: b8c9d0e1f2a7
Revises: a7b8c9d0e1f6
Create Date: 2026-08-16 17:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a7"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_DISCORD_ID = "353089661175988224"
"""The default `SQUID_BOT_IDENTITY_OWNER_ID`, which is what the deleted allowlist held."""


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO permission_role_assignments (role_id, subject_account_id, reason) "
            "SELECT role.id, identity.account_id, 'Seeded from the retired staff notification allowlist' "
            "FROM permission_roles AS role "
            "JOIN account_identities AS identity "
            "  ON identity.provider = 'discord' AND identity.subject = :owner "
            "WHERE role.builtin_key = 'global-admin' "
            "ON CONFLICT DO NOTHING"
        ).bindparams(owner=_OWNER_DISCORD_ID)
    )
    op.drop_column("notification_deliveries", "discord_id")


def downgrade() -> None:
    """Re-add the address column and refill it from the identity it was a copy of.

    The seeded role assignment is deliberately left in place: it is indistinguishable
    from one an operator made by hand, and removing it would revoke real access.
    """
    op.add_column("notification_deliveries", sa.Column("discord_id", sa.BigInteger(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE notification_deliveries SET discord_id = identity.subject::bigint "
            "FROM account_identities AS identity "
            "WHERE identity.provider = 'discord' AND identity.account_id = notification_deliveries.account_id"
        )
    )
