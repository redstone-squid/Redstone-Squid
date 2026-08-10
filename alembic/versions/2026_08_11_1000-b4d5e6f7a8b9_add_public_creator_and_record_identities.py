"""Add stable public creator and record competition identities.

Revision ID: b4d5e6f7a8b9
Revises: a3c4e5f6a7b8
Create Date: 2026-08-11 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "a3c4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create opaque creator IDs and durable logical record IDs."""
    op.add_column(
        "users",
        sa.Column(
            "public_creator_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("users_public_creator_id_key", "users", ["public_creator_id"])

    op.create_table(
        "record_competitions",
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("record_class", sa.Text(), nullable=False),
        sa.Column("build_kind", sa.Text(), nullable=False),
        sa.Column("version_scope", sa.Text(), nullable=False),
        sa.Column("version_id", sa.SmallInteger(), nullable=True),
        sa.Column("category_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "record_class IN ('first', 'fastest', 'smallest', 'fastest_smallest', 'smallest_fastest')",
            name="record_competitions_record_class_check",
        ),
        sa.CheckConstraint(
            "version_scope IN ('all_time', 'current')",
            name="record_competitions_version_scope_check",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["versions.id"],
            name="record_competitions_version_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("public_id"),
        sa.UniqueConstraint(
            "record_class",
            "build_kind",
            "version_scope",
            "version_id",
            "category_key",
            name="record_competitions_identity_key",
            postgresql_nulls_not_distinct=True,
        ),
        comment="A stable public identity for a logical record competition across rulesets.",
    )
    op.execute(
        "COMMENT ON TABLE record_definitions IS 'A ruleset-specific definition of one stable record competition.'"
    )
    op.execute(
        """
        INSERT INTO record_competitions
            (record_class, build_kind, version_scope, version_id, category_key)
        SELECT DISTINCT
            record_class, build_kind, version_scope, version_id, category_key
        FROM record_definitions
        """
    )
    op.add_column(
        "record_definitions",
        sa.Column("competition_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE record_definitions AS definitions
        SET competition_id = competitions.public_id
        FROM record_competitions AS competitions
        WHERE competitions.record_class = definitions.record_class
          AND competitions.build_kind = definitions.build_kind
          AND competitions.version_scope = definitions.version_scope
          AND competitions.version_id IS NOT DISTINCT FROM definitions.version_id
          AND competitions.category_key = definitions.category_key
        """
    )
    op.alter_column("record_definitions", "competition_id", nullable=False)
    op.create_foreign_key(
        "record_definitions_competition_id_fkey",
        "record_definitions",
        "record_competitions",
        ["competition_id"],
        ["public_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Remove public identities while preserving record definitions and accounts."""
    op.drop_constraint("record_definitions_competition_id_fkey", "record_definitions", type_="foreignkey")
    op.drop_column("record_definitions", "competition_id")
    op.execute("COMMENT ON TABLE record_definitions IS 'A stable identity for one record competition.'")
    op.drop_table("record_competitions")
    op.drop_constraint("users_public_creator_id_key", "users", type_="unique")
    op.drop_column("users", "public_creator_id")
