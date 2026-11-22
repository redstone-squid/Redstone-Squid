"""Refresh search documents with record category filters.

Revision ID: b9e5d016c4a2
Revises: e3af0afeec12
Create Date: 2026-11-22 13:00:00+00:00
"""

from alembic import op

revision: str = "b9e5d016c4a2"
down_revision: str | None = "e3af0afeec12"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        SELECT public.upsert_search_projection_request('record', 'result:' || result.id::text, 'upsert')
        FROM public.title_results result
        JOIN public.record_computation_runs run ON run.id = result.run_id
        WHERE run.is_active
        ORDER BY result.id
        """
    )


def downgrade() -> None:
    # The worker from the restored application version will rebuild the old representation.
    upgrade()
