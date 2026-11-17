"""Keep concrete computation inputs in memory instead of storing full copies."""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f6bad4e8a273"
down_revision: str | None = "e5a9c3d7f162"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTION = """
CREATE FUNCTION public.reject_record_input_snapshot_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Record input snapshots are immutable' USING ERRCODE = '23514';
END;
$$;
"""

_TRIGGER = """
CREATE TRIGGER record_input_snapshots_immutable
BEFORE UPDATE OR DELETE ON public.record_input_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_record_input_snapshot_mutation();
"""


def upgrade() -> None:
    for name in ("input_snapshot_id", "checked_snapshot_id"):
        op.drop_constraint(f"record_computation_runs_{name}_fkey", "record_computation_runs", type_="foreignkey")
        op.drop_column("record_computation_runs", name)
    op.drop_entity(PGTrigger.from_sql(_TRIGGER.strip()))
    op.drop_entity(PGFunction.from_sql(_FUNCTION.strip()))
    op.drop_table("record_input_snapshots")


def downgrade() -> None:
    # Restore the schema; discarded input copies cannot be reconstructed.
    op.create_table(
        "record_input_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(jsonb_typeof(document) = 'object' AND document->'schema_version' = '1'::jsonb) IS TRUE",
            name="record_input_snapshots_version_check",
        ),
        comment="Immutable concrete inputs saved before arithmetic starts.",
    )
    for name in ("input_snapshot_id", "checked_snapshot_id"):
        op.add_column("record_computation_runs", sa.Column(name, sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"record_computation_runs_{name}_fkey",
            "record_computation_runs",
            "record_input_snapshots",
            [name],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_entity(PGFunction.from_sql(_FUNCTION.strip()))
    op.create_entity(PGTrigger.from_sql(_TRIGGER.strip()))
