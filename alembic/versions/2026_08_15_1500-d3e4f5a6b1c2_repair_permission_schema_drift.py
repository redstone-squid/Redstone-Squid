"""Repair permission schema drift

`alembic check` has been failing on a clean database: the permission models'
docstrings were expanded after the tables were created, and `permission_epoch.id`
carries a `nextval` default the model never declared.

The serial default is not merely cosmetic. `permission_epoch` is a singleton
guarded by `CHECK (id = 1)`, so an insert that let the sequence pick the id would
draw 2 and fail the check. The model is right and the database is wrong.

Revision ID: d3e4f5a6b1c2
Revises: c2d3e4f5a6b1
Create Date: 2026-08-15 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e4f5a6b1c2"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_COMMENTS = {
    "permission_audit_log": (
        "An append-only record of one permission mutation.\n\n"
        "Written in the same transaction as the change it describes. The repository\n"
        "exposes no update or delete path for this table."
    ),
    "permission_epoch": (
        "A single counter bumped by any permission write.\n\n"
        "Three processes each hold their own rule-set cache, so a grant issued in the\n"
        "API has to become visible in the bot. A trigger bumps this row and sends\n"
        "`NOTIFY squid_permissions`; watchers treat the notification as a latency hint\n"
        "and poll this counter as the durable signal."
    ),
    "permission_role_patterns": (
        "One pattern a role includes or subtracts.\n\n"
        "Subtraction is not a deny: it withholds the pattern from *this* role's\n"
        "contribution, and any other role including it still confers it. That is\n"
        "Azure's `NotActions` semantics, and it is why a role can be written as \"this\n"
        'namespace, minus its destructive members" without poisoning other grants.'
    ),
    "permission_roles": (
        "A named bundle of permission patterns.\n\n"
        "Built-in roles keep their pattern lists in code, so this row exists only to\n"
        "give assignments a foreign key; any `permission_role_patterns` rows attached\n"
        "to a built-in are additive overrides on top of the code-defined list."
    ),
}

_SHORT_TABLE_COMMENTS = {
    "permission_audit_log": "An append-only record of one permission mutation.",
    "permission_epoch": "A single counter bumped by any permission write.",
    "permission_role_patterns": "One pattern a role includes or subtracts.",
    "permission_roles": "A named bundle of permission patterns.",
}


def upgrade() -> None:
    """Apply this revision."""
    for table, comment in _TABLE_COMMENTS.items():
        op.create_table_comment(table, comment, existing_comment=_SHORT_TABLE_COMMENTS[table], schema=None)
    op.alter_column(
        "permission_epoch",
        "id",
        existing_type=sa.SmallInteger(),
        server_default=None,
        existing_nullable=False,
    )
    op.execute("DROP SEQUENCE IF EXISTS public.permission_epoch_id_seq")


def downgrade() -> None:
    """Revert this revision when the operation is safe."""
    op.execute("CREATE SEQUENCE IF NOT EXISTS public.permission_epoch_id_seq AS smallint OWNED BY permission_epoch.id")
    op.alter_column(
        "permission_epoch",
        "id",
        existing_type=sa.SmallInteger(),
        server_default=sa.text("nextval('permission_epoch_id_seq'::regclass)"),
        existing_nullable=False,
    )
    for table, comment in _SHORT_TABLE_COMMENTS.items():
        op.create_table_comment(table, comment, existing_comment=_TABLE_COMMENTS[table], schema=None)
