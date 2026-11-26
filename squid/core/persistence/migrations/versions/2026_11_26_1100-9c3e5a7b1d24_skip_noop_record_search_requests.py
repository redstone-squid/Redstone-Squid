"""Skip record search requests that would do nothing.

The previous revision deleted superseded results in bulk. Its row triggers queued a
delete for every result and an upsert for every holder, about 579,000 requests, and
almost none had a document to change. Useful work waited behind them for over a day.
A deleted result now queues a delete only when it has a document or a pending
request, and a holder only queues work while its result still exists.

Revision ID: 9c3e5a7b1d24
Revises: 7d2c9e41b6a8
Create Date: 2026-11-26 11:00:00+00:00
"""

from alembic_utils.pg_function import PGFunction

from alembic import op

revision: str = "9c3e5a7b1d24"
down_revision: str | None = "7d2c9e41b6a8"
branch_labels: str | None = None
depends_on: str | None = None

_SEARCH = "CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_key text;\nBEGIN\n    IF TG_TABLE_NAME = 'title_results' THEN\n        target_key := 'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text;\n        -- One statement, so a worker committing the document and its acknowledgement\n        -- together cannot slip between the two checks.\n        IF TG_OP = 'DELETE' AND NOT EXISTS (\n            SELECT 1 FROM public.search_documents d\n            WHERE d.resource_kind = 'record' AND d.source_key = target_key\n            UNION ALL\n            SELECT 1 FROM public.search_projection_queue q\n            WHERE q.resource_kind = 'record' AND q.source_key = target_key\n        ) THEN\n            RETURN NULL;\n        END IF;\n        PERFORM public.upsert_search_projection_request(\n            'record', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END\n        );\n    ELSIF EXISTS (\n        SELECT 1 FROM public.title_results r\n        WHERE r.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END\n    ) THEN\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,\n            'upsert'\n        );\n    END IF;\n    RETURN NULL;\nEND;\n$$;"

_OLD_SEARCH = "CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nBEGIN\n    IF TG_TABLE_NAME = 'title_results' THEN\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text,\n            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END\n        );\n    ELSE\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,\n            'upsert'\n        );\n    END IF;\n    RETURN NULL;\nEND;\n$$;"


def upgrade() -> None:
    op.replace_entity(PGFunction.from_sql(_SEARCH))


def downgrade() -> None:
    op.replace_entity(PGFunction.from_sql(_OLD_SEARCH))
