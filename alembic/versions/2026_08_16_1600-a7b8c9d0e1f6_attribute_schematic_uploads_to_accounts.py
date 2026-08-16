"""Attribute schematic uploads to accounts

`build_schematics.uploaded_by_discord_id` was a denormalized copy of an identity already
reachable through `account_identities`, and it sat two columns away from
`rights_attested_by_account_id` -- so one table carried two attribution styles for the
same kind of fact. Landing `uploaded_by_account_id` beside it leaves one.

`ON DELETE SET NULL` rather than the attestation column's `RESTRICT`: an attestation is a
legal record that must not be silently orphaned, while "who supplied the file" is
provenance, and losing it should not block deleting an account.

Revision ID: a7b8c9d0e1f6
Revises: f6a7b8c9d0e5
Create Date: 2026-08-16 16:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f6"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("build_schematics", sa.Column("uploaded_by_account_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "build_schematics_uploaded_by_account_id_fkey",
        "build_schematics",
        "accounts",
        ["uploaded_by_account_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        sa.text(
            "UPDATE build_schematics SET uploaded_by_account_id = identity.account_id "
            "FROM account_identities AS identity "
            "WHERE identity.provider = 'discord' "
            "AND identity.subject = build_schematics.uploaded_by_discord_id::text"
        )
    )
    op.drop_column("build_schematics", "uploaded_by_discord_id")


def downgrade() -> None:
    op.add_column("build_schematics", sa.Column("uploaded_by_discord_id", sa.BigInteger(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE build_schematics SET uploaded_by_discord_id = identity.subject::bigint "
            "FROM account_identities AS identity "
            "WHERE identity.provider = 'discord' "
            "AND identity.account_id = build_schematics.uploaded_by_account_id"
        )
    )
    op.drop_constraint("build_schematics_uploaded_by_account_id_fkey", "build_schematics", type_="foreignkey")
    op.drop_column("build_schematics", "uploaded_by_account_id")
