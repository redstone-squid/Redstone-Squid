"""roll out title formatter v2

Revision ID: c9b2d861f540
Revises: e8a6b347c921
Create Date: 2026-07-30 15:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c9b2d861f540"
down_revision: str | Sequence[str] | None = "e8a6b347c921"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENT_HASH = "312af53ee50a0cb0cee37673a763a6072321039777451997acc23cb26a6ba9ac"
_ENQUEUE_BUILD_SEARCH_PROJECTION = """
CREATE OR REPLACE FUNCTION public.enqueue_build_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_build_id bigint;
    target_action text := 'upsert';
    target_kind text;
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);
        IF TG_OP = 'DELETE' THEN
            target_action := 'delete';
        END IF;
    ELSE
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('build', target_build_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;

    IF target_kind IN ('door', 'extender') THEN
        INSERT INTO public.record_recompute_queue
            (scope_key, build_kind, build_id, reasons, enqueued_at)
        VALUES (
            target_kind,
            target_kind,
            CASE WHEN TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE' THEN NULL ELSE target_build_id END,
            '["source_change"]'::jsonb,
            now()
        )
        ON CONFLICT (scope_key) DO UPDATE
        SET build_id = EXCLUDED.build_id,
            reasons = (
                SELECT jsonb_agg(DISTINCT reason)
                FROM jsonb_array_elements_text(
                    record_recompute_queue.reasons || EXCLUDED.reasons
                ) AS reason
            ),
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;
"""


def upgrade() -> None:
    """Persist canonical definition titles and activate formatter v2."""
    op.execute(_ENQUEUE_BUILD_SEARCH_PROJECTION)
    op.add_column("record_definitions", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("record_definitions", sa.Column("subtitle", sa.Text(), nullable=True))
    op.add_column(
        "record_definitions",
        sa.Column(
            "title_diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    op.execute(
        """
        WITH holder_titles AS (
            SELECT DISTINCT ON (rr.definition_id)
                rr.definition_id,
                rrh.title,
                rrh.subtitle
            FROM record_results AS rr
            JOIN record_result_holders AS rrh ON rrh.result_id = rr.id
            ORDER BY rr.definition_id, rr.computed_at DESC, rrh.rank, rrh.build_id
        )
        UPDATE record_definitions AS rd
        SET title = COALESCE(
                holder_titles.title,
                initcap(replace(rd.record_class, '_', ' ')) || ' ' || rd.category_key
            ),
            subtitle = holder_titles.subtitle
        FROM holder_titles
        WHERE holder_titles.definition_id = rd.id
        """
    )
    op.execute(
        """
        UPDATE record_definitions
        SET title = initcap(replace(record_class, '_', ' ')) || ' ' || category_key
        WHERE title IS NULL
        """
    )
    op.alter_column("record_definitions", "title", existing_type=sa.Text(), nullable=False)

    op.drop_index("record_computation_runs_one_active_idx", table_name="record_computation_runs")
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY build_kind, version_id
                    ORDER BY completed_at DESC NULLS LAST, started_at DESC, id DESC
                ) AS active_rank
            FROM record_computation_runs
            WHERE is_active
        )
        UPDATE record_computation_runs AS run
        SET is_active = false
        FROM ranked
        WHERE run.id = ranked.id
          AND ranked.active_rank > 1
        """
    )
    op.create_index(
        "record_computation_runs_one_active_idx",
        "record_computation_runs",
        ["build_kind", "version_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )

    op.execute("UPDATE record_rulesets SET activated_at = NULL WHERE activated_at IS NOT NULL")
    op.execute(
        sa.text(
            """
            INSERT INTO record_rulesets (
                document_hash,
                calculator_version,
                formatter_version,
                activated_at
            )
            VALUES (:document_hash, '1', '2', now())
            ON CONFLICT (document_hash, calculator_version, formatter_version)
            DO UPDATE SET activated_at = EXCLUDED.activated_at
            """
        ).bindparams(document_hash=_DOCUMENT_HASH)
    )

    op.execute(
        """
        INSERT INTO record_recompute_queue (scope_key, build_kind, reasons, enqueued_at)
        VALUES
            ('door', 'door', '["formatter_v2"]'::jsonb, now()),
            ('extender', 'extender', '["formatter_v2"]'::jsonb, now())
        ON CONFLICT (scope_key) DO UPDATE
        SET reasons = (
                SELECT jsonb_agg(DISTINCT reason)
                FROM jsonb_array_elements_text(
                    record_recompute_queue.reasons || EXCLUDED.reasons
                ) AS reason
            ),
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            last_error = NULL
        """
    )
    op.execute(
        """
        INSERT INTO search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'record', 'result:' || id::text, 'upsert', now()
        FROM record_results
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action,
            enqueued_at = EXCLUDED.enqueued_at,
            attempts = 0,
            locked_at = NULL,
            last_error = NULL
        """
    )


def downgrade() -> None:
    """Restore ruleset-scoped activation and remove definition title fields."""
    op.drop_index("record_computation_runs_one_active_idx", table_name="record_computation_runs")
    op.create_index(
        "record_computation_runs_one_active_idx",
        "record_computation_runs",
        ["ruleset_id", "build_kind", "version_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        postgresql_nulls_not_distinct=True,
    )
    op.drop_column("record_definitions", "title_diagnostics")
    op.drop_column("record_definitions", "subtitle")
    op.drop_column("record_definitions", "title")
