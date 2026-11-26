"""Queue records from the documents they read, instead of from the worker.

The projection worker queued a build's records after every refresh of the build,
and a tag's records after every refresh of the tag, whether or not anything had
changed. A statement-level trigger on search_documents now queues them when a
build or tag document's title, status or data changes. Unchanged documents are
not rewritten, so refreshing an unchanged build queues nothing.

Revision ID: 5d2b8e4f1a63
Revises: 3f8a1c6e9b27
Create Date: 2026-11-26 16:00:00+00:00
"""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision: str = "5d2b8e4f1a63"
down_revision: str | None = "3f8a1c6e9b27"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTION = "CREATE FUNCTION public.enqueue_dependent_record_search() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    kinds text[];\n    keys text[];\nBEGIN\n    IF TG_OP = 'INSERT' THEN\n        SELECT array_agg(resource_kind), array_agg(source_key) INTO kinds, keys FROM new_rows;\n    ELSIF TG_OP = 'DELETE' THEN\n        SELECT array_agg(resource_kind), array_agg(source_key) INTO kinds, keys FROM old_rows;\n    ELSE\n        SELECT array_agg(n.resource_kind), array_agg(n.source_key) INTO kinds, keys\n        FROM new_rows n JOIN old_rows o ON o.id = n.id\n        WHERE (n.title, n.status, n.document_data) IS DISTINCT FROM (o.title, o.status, o.document_data);\n    END IF;\n    IF kinds IS NULL THEN\n        RETURN NULL;\n    END IF;\n    -- CASE keeps the casts away from keys of other kinds.\n    PERFORM public.upsert_search_projection_requests(\n        'record',\n        ARRAY(\n            SELECT 'record:' || r.title_id::text\n            FROM unnest(kinds, keys) AS changed(kind, key)\n            JOIN public.title_holders h\n                ON h.build_id = CASE WHEN changed.kind = 'build' AND changed.key ~ '^[0-9]+$' THEN changed.key::bigint END\n            JOIN public.title_results r ON r.id = h.result_id\n            UNION\n            SELECT d.source_key\n            FROM unnest(kinds, keys) AS changed(kind, key)\n            JOIN public.search_values v\n                ON v.field_name = 'category_tag_id'\n                AND v.numeric_value = CASE\n                    WHEN changed.kind = 'metadata' AND changed.key ~ '^tag:[0-9]+$'\n                    THEN split_part(changed.key, ':', 2)::numeric\n                END\n            JOIN public.search_documents d ON d.id = v.document_id AND d.resource_kind = 'record'\n        ),\n        'upsert'\n    );\n    RETURN NULL;\nEND;\n$$;"

_TRIGGERS = (
    "CREATE TRIGGER search_documents_enqueue_dependents_delete\n    AFTER DELETE ON public.search_documents\n    REFERENCING OLD TABLE AS old_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_dependent_record_search();",
    "CREATE TRIGGER search_documents_enqueue_dependents_insert\n    AFTER INSERT ON public.search_documents\n    REFERENCING NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_dependent_record_search();",
    "CREATE TRIGGER search_documents_enqueue_dependents_update\n    AFTER UPDATE ON public.search_documents\n    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_dependent_record_search();",
)


def upgrade() -> None:
    op.create_entity(PGFunction.from_sql(_FUNCTION))
    for trigger in _TRIGGERS:
        op.create_entity(PGTrigger.from_sql(trigger))


def downgrade() -> None:
    for trigger in _TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(trigger))
    op.drop_entity(PGFunction.from_sql(_FUNCTION))
