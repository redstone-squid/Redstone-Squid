"""Refresh door search documents with opening dimension facets.

Revision ID: e8f2a4b0c635
Revises: d7e1f3a9b524
Create Date: 2026-11-16 12:00:00+00:00
"""

from alembic import op

revision: str = "e8f2a4b0c635"
down_revision: str | None = "d7e1f3a9b524"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        SELECT public.upsert_search_projection_request('build', door.build_id::text, 'upsert')
        FROM public.doors door
        """
    )


def downgrade() -> None:
    # The restored application worker rebuilds documents without these facets.
    upgrade()
