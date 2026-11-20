"""Refresh build and tag search documents with alias vocabulary.

Revision ID: f9a3b5c1d746
Revises: e8f2a4b0c635
Create Date: 2026-11-16 13:00:00+00:00
"""

from alembic import op

revision: str = "f9a3b5c1d746"
down_revision: str | None = "e8f2a4b0c635"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        SELECT public.upsert_search_projection_request('build', id::text, 'upsert') FROM public.builds
    """)
    op.execute("""
        SELECT public.upsert_search_projection_request('metadata', 'tag:' || id::text, 'upsert')
        FROM public.tag_definitions
    """)


def downgrade() -> None:
    # The previous worker restores documents without the alias vocabulary.
    upgrade()
