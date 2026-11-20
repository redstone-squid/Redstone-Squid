"""Refresh build search documents so their titles include miscellaneous restrictions.

Revision ID: d7e1f3a9b524
Revises: c4f8b2e6a0d3
"""

from alembic import op

revision: str = "d7e1f3a9b524"
down_revision: str | None = "c4f8b2e6a0d3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Projection now indexes each build's one-line title; queue every build so stored documents match.
    op.execute("""
        SELECT public.upsert_search_projection_request('build', id::text, 'upsert') FROM public.builds
    """)


def downgrade() -> None:
    """The queued refreshes are harmless under the previous projection, so nothing is undone."""
