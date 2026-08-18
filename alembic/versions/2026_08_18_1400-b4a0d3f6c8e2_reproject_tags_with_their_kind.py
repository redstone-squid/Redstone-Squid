"""Reproject tag documents so search can tell restrictions from patterns

Every approved tag definition -- restrictions, patterns, and showcase tags alike -- projected
into the search index as `metadata_kind = 'tag'` with a single `kind = tag` facet. A reader
asking for patterns therefore had no way to ask for them: `/patterns search` and
`/restrictions search` existed precisely because the index could not answer the question.

The projection now records the definition's `semantic_kind`, so `kind:pattern` and
`kind:restriction` work and a result can say what it is. `kind:tag` is emitted as well, so the
one query that was previously possible keeps working.

Documents already in the index carry the old shape, and nothing about the tag rows themselves
changed -- so no trigger will fire and no backfill of the documents is possible in SQL, because
the projection is built in Python. Re-enqueueing every tag definition makes the existing worker
rebuild them, at one queue row per definition. `DO NOTHING` because a definition already queued
for its own reason will be rebuilt with the new code regardless.

Revision ID: b4a0d3f6c8e2
Revises: a3f9c2e5b7d1
Create Date: 2026-08-18 14:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4a0d3f6c8e2"
down_revision: str | Sequence[str] | None = "a3f9c2e5b7d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Queue a projection refresh for every tag definition."""
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'metadata', 'tag:' || id::text, 'upsert' FROM public.tag_definitions
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Queue the same refresh, which the older code rebuilds in the older shape."""
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'metadata', 'tag:' || id::text, 'upsert' FROM public.tag_definitions
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )
