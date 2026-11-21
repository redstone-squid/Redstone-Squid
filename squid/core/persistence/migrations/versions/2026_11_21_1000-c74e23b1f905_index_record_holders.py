"""Index current record holders and their credits.

Revision ID: c74e23b1f905
Revises: a325bc2746d1
Create Date: 2026-11-21 10:00:00+00:00
"""

from alembic import op

revision: str = "c74e23b1f905"
down_revision: str | None = "a325bc2746d1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        SELECT public.upsert_search_projection_request('record', 'result:' || result.id::text, 'upsert')
        FROM public.title_results result
        JOIN public.record_computation_runs run ON run.id = result.run_id
        WHERE run.is_active
        """
    )


def downgrade() -> None:
    # The worker from the restored application version will rebuild the old representation.
    upgrade()
