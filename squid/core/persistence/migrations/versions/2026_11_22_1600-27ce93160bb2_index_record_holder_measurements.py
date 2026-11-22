"""Index measurements by published record holder.

Revision ID: 27ce93160bb2
Revises: 3c8e1f5a9d27
Create Date: 2026-11-22 16:00:00+00:00
"""

import sqlalchemy as sa

from alembic import op

revision: str = "27ce93160bb2"
down_revision: str | None = "3c8e1f5a9d27"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("search_document_filters", sa.Column("holder_build_id", sa.BigInteger(), nullable=True))
    op.create_index(
        "search_document_filters_holder_idx",
        "search_document_filters",
        ["document_id", "holder_build_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("holder_build_id IS NOT NULL"),
    )
    op.execute(
        """
        SELECT public.upsert_search_projection_request('record', 'result:' || result.id::text, 'upsert')
        FROM public.title_results result
        JOIN public.record_computation_runs run ON run.id = result.run_id
        WHERE run.is_active
        ORDER BY result.id
        """
    )
    # These scopes survive ordinary edits while also refreshing explicitly published versions.
    op.execute(
        """
        INSERT INTO public.record_recompute_queue (scope_key, build_kind, reasons)
        SELECT 'holder_measurements:' || build_kind, build_kind, '["holder_measurements"]'::jsonb
        FROM public.record_computation_runs
        WHERE is_active AND build_kind IN ('door', 'extender')
        GROUP BY build_kind
        ON CONFLICT (scope_key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public.record_recompute_queue WHERE reasons ? 'holder_measurements'")
    op.execute(
        "DELETE FROM public.search_document_filters WHERE holder_build_id IS NOT NULL AND field_name <> 'holder'"
    )
    op.drop_index("search_document_filters_holder_idx", table_name="search_document_filters")
    op.drop_column("search_document_filters", "holder_build_id")
