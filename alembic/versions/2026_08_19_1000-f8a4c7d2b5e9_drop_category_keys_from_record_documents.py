"""Reproject record documents without their internal category keys

Record search documents carried the full category key
(door:door|2x2|t[20]|Door:r[...]:p[...]) in their public tags, and a
definition with no holders used it as the document title. The projection now
emits only readable tags and falls back to the definition's formatted title,
but documents for the currently active runs are only rebuilt when a new run
replaces them — so without a nudge the stale tags would sit in /search and
GET /v1/search until the next rebuild of each kind.

Re-enqueueing every active result makes the existing worker rebuild them with
the new shape; the changed source hash re-queues embeddings on its own.
`DO NOTHING` because a result already queued for its own reason will be
rebuilt with the new code regardless.

Revision ID: f8a4c7d2b5e9
Revises: e7d3a6c9f4b2
Create Date: 2026-08-19 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f8a4c7d2b5e9"
down_revision: str | Sequence[str] | None = "e7d3a6c9f4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Queue a projection refresh for every active record result."""
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'record', 'result:' || rr.id::text, 'upsert'
        FROM public.record_results rr
        JOIN public.record_computation_runs rcr ON rcr.id = rr.run_id
        WHERE rcr.is_active
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Queue the same refresh, which the older code rebuilds in the older shape."""
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'record', 'result:' || rr.id::text, 'upsert'
        FROM public.record_results rr
        JOIN public.record_computation_runs rcr ON rcr.id = rr.run_id
        WHERE rcr.is_active
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )
