"""Refresh registered ranges independently of concrete record publications."""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2d6f0a4c839"
down_revision: str | None = "a1c5e9f3b728"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTION = """
CREATE FUNCTION public.enqueue_registered_door_ranges() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.build_kind = 'door' THEN
        INSERT INTO public.door_range_recompute_queue (query_id)
        SELECT id FROM public.door_range_registrations ORDER BY id
        ON CONFLICT (query_id) DO UPDATE SET
            enqueued_at = now(), available_at = now(),
            claimed_at = NULL, claim_token = NULL;
    END IF;
    RETURN NULL;
END;
$$;
"""
_TRIGGER = """
CREATE TRIGGER record_recompute_queue_enqueue_ranges
AFTER INSERT OR UPDATE OF enqueued_at ON public.record_recompute_queue
FOR EACH ROW EXECUTE FUNCTION public.enqueue_registered_door_ranges();
"""


def upgrade() -> None:
    op.create_table(
        "door_range_recompute_queue",
        sa.Column("query_id", sa.BigInteger(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("query_id"),
        sa.ForeignKeyConstraint(["query_id"], ["door_range_registrations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("attempts >= 0", name="door_range_recompute_queue_attempts_nonnegative"),
        sa.CheckConstraint(
            "(claimed_at IS NULL) = (claim_token IS NULL)", name="door_range_recompute_queue_claim_complete"
        ),
        comment="One independently retried refresh request per registered door range.",
    )
    op.create_entity(PGFunction.from_sql(_FUNCTION.strip()))
    op.create_entity(PGTrigger.from_sql(_TRIGGER.strip()))
    op.execute("INSERT INTO door_range_recompute_queue (query_id) SELECT id FROM door_range_registrations")


def downgrade() -> None:
    op.drop_entity(PGTrigger.from_sql(_TRIGGER.strip()))
    op.drop_entity(PGFunction.from_sql(_FUNCTION.strip()))
    op.drop_table("door_range_recompute_queue")
