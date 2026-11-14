"""Store immutable door range snapshots and fenced publications.

Revision ID: b3e7a1d5c9f2
Revises: f2d5a8c1e4b7
"""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3e7a1d5c9f2"
down_revision: str | None = "f2d5a8c1e4b7"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTION = """
CREATE OR REPLACE FUNCTION public.reject_door_range_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Door range snapshots are immutable' USING ERRCODE = '23514';
END;
$$;
"""
_TRIGGER = """
CREATE TRIGGER door_range_snapshots_immutable
BEFORE UPDATE OR DELETE ON public.door_range_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_door_range_snapshot_mutation();
"""


def upgrade() -> None:
    op.create_table(
        "door_range_registrations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("query_key", sa.Text(), nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("generation >= 0", name="door_range_registrations_generation_check"),
        sa.PrimaryKeyConstraint("id", name=op.f("door_range_registrations_pkey")),
        sa.UniqueConstraint("query_key", name="door_range_registrations_query_key_key"),
        comment="A registered range query and its latest reserved computation generation.",
    )
    op.create_table(
        "door_range_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("rules_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint(
            "NOT is_active OR (result->'analysis'->>'status' = 'exact') IS TRUE", name="door_range_runs_active_check"
        ),
        sa.CheckConstraint(
            "(result IS NOT NULL OR error IS NOT NULL) = (completed_at IS NOT NULL)",
            name="door_range_runs_completion_check",
        ),
        sa.CheckConstraint("generation > 0", name="door_range_runs_generation_check"),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["door_range_registrations.id"],
            name=op.f("door_range_runs_query_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rules_revision_id"],
            ["rules_revisions.id"],
            name=op.f("door_range_runs_rules_revision_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("door_range_runs_pkey")),
        sa.UniqueConstraint("query_id", "generation", name="door_range_runs_generation_key"),
        comment="A reserved attempt whose complete result can replace one registered query.",
    )
    op.create_index(
        "door_range_runs_one_active_idx",
        "door_range_runs",
        ["query_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "door_range_snapshots",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(jsonb_typeof(document) = 'object' AND document->'schema_version' = '1'::jsonb) IS TRUE",
            name="door_range_snapshots_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["door_range_runs.id"], name=op.f("door_range_snapshots_run_id_fkey"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("door_range_snapshots_pkey")),
        comment="Immutable arithmetic facts captured before a range worker starts.",
    )
    op.create_entity(PGFunction.from_sql(_FUNCTION))
    op.create_entity(PGTrigger.from_sql(_TRIGGER.strip()))


def downgrade() -> None:
    op.drop_entity(PGTrigger.from_sql(_TRIGGER.strip()))
    op.drop_entity(PGFunction.from_sql(_FUNCTION))
    op.drop_table("door_range_snapshots")
    op.drop_table("door_range_runs")
    op.drop_table("door_range_registrations")
