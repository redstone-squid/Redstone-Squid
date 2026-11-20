"""remove legacy smallest door records

Revision ID: 3c8b1f0a6d24
Revises: 9a1e4c7b3d62
Create Date: 2026-07-31 11:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3c8b1f0a6d24"
down_revision: str | Sequence[str] | None = "9a1e4c7b3d62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the superseded trigger-maintained smallest-door cache."""
    op.execute(
        """
        INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons, enqueued_at)
        VALUES ('door', 'door', '["legacy_cache_retirement"]'::jsonb, now())
        ON CONFLICT (scope_key) DO UPDATE
        SET reasons = record_recompute_queue.reasons || EXCLUDED.reasons,
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            last_error = NULL
        """
    )
    op.execute(
        """
        DELETE FROM public.search_projection_queue
        WHERE resource_kind = 'record'
          AND source_key LIKE 'legacy-smallest:%'
        """
    )
    op.execute(
        """
        DELETE FROM public.search_documents
        WHERE resource_kind = 'record'
          AND source_key LIKE 'legacy-smallest:%'
        """
    )

    op.execute("DROP TRIGGER IF EXISTS build_restrictions_refresh_smallest_door ON public.build_restrictions")
    op.execute("DROP TRIGGER IF EXISTS build_types_refresh_smallest_door ON public.build_types")
    op.execute("DROP TRIGGER IF EXISTS builds_refresh_smallest_door ON public.builds")
    op.execute("DROP TRIGGER IF EXISTS doors_refresh_smallest_door ON public.doors")
    op.execute("DROP TRIGGER IF EXISTS smallest_door_records_enqueue_search ON public.smallest_door_records")

    op.execute("DROP FUNCTION IF EXISTS public.enqueue_legacy_record_search_projection()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_refresh_smallest_door()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_refresh_smallest_door_from_builds()")
    op.execute("DROP PROCEDURE IF EXISTS public.rebuild_smallest_door_records()")
    op.execute("DROP PROCEDURE IF EXISTS public.refresh_smallest_after_door_delete(bigint)")
    op.execute("DROP PROCEDURE IF EXISTS public.refresh_smallest_for_door_insert(bigint)")
    op.drop_table("smallest_door_records")


def downgrade() -> None:
    """Reject restoration of the retired cache and its trigger graph."""
    msg = "The legacy smallest-door cache cannot be restored; rebuild records with the current calculator instead."
    raise RuntimeError(msg)
