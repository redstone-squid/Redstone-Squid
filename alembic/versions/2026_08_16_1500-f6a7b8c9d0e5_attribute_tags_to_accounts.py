"""Attribute tag definitions and assignments to accounts

`tag_definitions.created_by_discord_id` and `build_tag_assignments.created_by_discord_id`
were denormalized copies of an identity already reachable through `account_identities`,
and being snowflake-keyed made the ownership check for `assign_showcase` a join between
two Discord ids -- so an account with no Discord identity could not tag its own build.

The stable key is the second half. `f"user_{discord_id}_{hex}"` published a proposer's
snowflake verbatim as `BuildTag.key` in the API. The key is never parsed -- the only
literal comparison anywhere is against an *official* key -- so the format is free, and
leaving old rows alone would leave those snowflakes published.

**This invalidates bookmarked `?tag=user_...` queries.** That is the intended cost: the
alternative is keeping every already-published snowflake addressable forever.

Revision ID: f6a7b8c9d0e5
Revises: e5f6a7b8c9d4
Create Date: 2026-08-16 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e5"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("tag_definitions", "build_tag_assignments")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("created_by_account_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"{table}_created_by_account_id_fkey",
            table,
            "accounts",
            ["created_by_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.execute(
            sa.text(
                f"UPDATE {table} SET created_by_account_id = identity.account_id "
                "FROM account_identities AS identity "
                f"WHERE identity.provider = 'discord' AND identity.subject = {table}.created_by_discord_id::text"
            )
        )
        op.drop_column(table, "created_by_discord_id")

    op.execute(
        sa.text(
            "UPDATE tag_definitions "
            "SET stable_key = 'user_' || replace(gen_random_uuid()::text, '-', '') "
            "WHERE stable_key ~ '^user_[0-9]+_'"
        )
    )


def downgrade() -> None:
    """Restore the snowflake columns, refilled from the accounts they replaced.

    The rewritten stable keys are not restorable: the snowflake they embedded is gone
    from the key by construction, and re-deriving one would invent an attribution.
    """
    for table in _TABLES:
        op.add_column(table, sa.Column("created_by_discord_id", sa.BigInteger(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE {table} SET created_by_discord_id = identity.subject::bigint "
                "FROM account_identities AS identity "
                f"WHERE identity.provider = 'discord' AND identity.account_id = {table}.created_by_account_id"
            )
        )
        op.drop_constraint(f"{table}_created_by_account_id_fkey", table, type_="foreignkey")
        op.drop_column(table, "created_by_account_id")
