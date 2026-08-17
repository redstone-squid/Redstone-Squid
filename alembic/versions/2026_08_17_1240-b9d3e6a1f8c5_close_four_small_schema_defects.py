"""Close four small schema defects

Four unrelated findings from the schema audit, none of which is large enough to deserve its own
revision and all of which are pure corrections to columns already in use.

`api_keys.created_by_account_id` had no `ON DELETE` action at all, so it defaulted to NO ACTION
while the sibling `owner_account_id` on the same table was SET NULL. Deleting an account that had
ever minted a key therefore failed outright, which is the opposite of what every other
`*_by_account_id` audit column in this schema does. SET NULL matches them and matches the column's
own docstring, which says the creator is recorded "when known".

`starboards.colour` was `BIGINT` for a value a CHECK constraint already bounds to
`0 .. 16777215` -- a 24-bit RGB triple. `INTEGER` holds it with room to spare.

`accounts.created_at`, `builds.submission_time` and `builds.edited_time` were the only three
nullable timestamp columns in the schema carrying a `now()` server default. Nothing can
legitimately read NULL from a column that defaults to the current time; they were nullable only
because the reflected baseline inherited it. Any straggling NULLs are backfilled from the row's
own defaults before the constraint goes on.

Revision ID: b9d3e6a1f8c5
Revises: a8c2d5f0e7b4
Create Date: 2026-08-17 12:40:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9d3e6a1f8c5"
down_revision: str | Sequence[str] | None = "a8c2d5f0e7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOT_NULL = (
    ("accounts", "created_at"),
    ("builds", "submission_time"),
    ("builds", "edited_time"),
)


def upgrade() -> None:
    """Apply this revision."""
    op.drop_constraint("api_keys_created_by_account_id_fkey", "api_keys", type_="foreignkey")
    op.create_foreign_key(
        "api_keys_created_by_account_id_fkey",
        "api_keys",
        "accounts",
        ["created_by_account_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column(
        "starboards",
        "colour",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("4415105"),
    )

    for table, column in _NOT_NULL:
        op.execute(f"UPDATE public.{table} SET {column} = now() WHERE {column} IS NULL")
        op.alter_column(table, column, existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    """Revert this revision."""
    for table, column in _NOT_NULL:
        op.alter_column(table, column, existing_type=sa.DateTime(timezone=True), nullable=True)

    op.alter_column(
        "starboards",
        "colour",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default=sa.text("4415105"),
    )

    op.drop_constraint("api_keys_created_by_account_id_fkey", "api_keys", type_="foreignkey")
    op.create_foreign_key(
        "api_keys_created_by_account_id_fkey",
        "api_keys",
        "accounts",
        ["created_by_account_id"],
        ["id"],
    )
