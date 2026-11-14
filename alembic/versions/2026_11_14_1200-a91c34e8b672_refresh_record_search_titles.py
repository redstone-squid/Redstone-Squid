"""Refresh record search titles and holder metrics.

Revision ID: a91c34e8b672
Revises: d89ea6c7c875
Create Date: 2026-11-14 12:00:00+00:00
"""

from alembic import op

revision: str = "a91c34e8b672"
down_revision: str | None = "d89ea6c7c875"
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
