"""Refresh build search documents with volume.

Revision ID: 6d9a04bcc27f
Revises: 24b3e7cd1a90
"""

from alembic import op

revision = "6d9a04bcc27f"
down_revision = "24b3e7cd1a90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        SELECT public.upsert_search_projection_request('build', id::text, 'upsert')
        FROM public.builds
        ORDER BY id
    """)


def downgrade() -> None:
    # Let the restored projection worker rebuild the previous document format.
    upgrade()
