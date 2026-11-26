"""Index search documents as typed field values.

Documents kept text in catch-all columns (tags, fuzzy_text, and three vectors),
so a match could not be traced to the field that produced it. Each searchable fact
is now a row in search_values, with a normalized form and a term vector for text,
and documents keep one weighted vector derived from those rows. Records are keyed
by their public ID, so retired records stay searchable and a revived record keeps
its key. Record triggers become statement-level, so a bulk change queues each
record once.

Every document is rebuilt: the index is emptied and every build, searchable
record, and metadata entry is queued. Search is incomplete until the worker
catches up.

Revision ID: 3f8a1c6e9b27
Revises: 9c3e5a7b1d24
Create Date: 2026-11-26 14:00:00+00:00
"""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "3f8a1c6e9b27"
down_revision: str | None = "9c3e5a7b1d24"
branch_labels: str | None = None
depends_on: str | None = None

_FUNCTIONS = (
    "CREATE FUNCTION public.upsert_search_projection_requests(\n    resource_kind_input text,\n    source_keys text[],\n    action_input text\n) RETURNS void\n    LANGUAGE sql\n    AS $$\n    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at, available_at)\n    SELECT resource_kind_input, keys.source_key, action_input, now(), now()\n    FROM (SELECT DISTINCT unnest(source_keys) AS source_key) AS keys\n    WHERE keys.source_key IS NOT NULL\n    ORDER BY keys.source_key\n    ON CONFLICT (resource_kind, source_key) DO UPDATE\n    SET action = EXCLUDED.action,\n        enqueued_at = EXCLUDED.enqueued_at,\n        available_at = EXCLUDED.available_at,\n        attempts = 0,\n        claimed_at = NULL,\n        claim_token = NULL,\n        dead_at = NULL,\n        last_error = NULL;\n$$;",
    "CREATE FUNCTION public.enqueue_record_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    record_ids uuid[];\nBEGIN\n    IF TG_TABLE_NAME IN ('title_results', 'title_holder_history') THEN\n        IF TG_OP = 'INSERT' THEN\n            record_ids := ARRAY(SELECT title_id FROM new_rows);\n        ELSIF TG_OP = 'DELETE' THEN\n            record_ids := ARRAY(SELECT title_id FROM old_rows);\n        ELSE\n            record_ids := ARRAY(SELECT title_id FROM new_rows UNION SELECT title_id FROM old_rows);\n        END IF;\n    ELSIF TG_TABLE_NAME IN ('title_holders', 'title_tied_builds') THEN\n        -- Rows deleted with their result find no result here; the result's own trigger queues the record.\n        IF TG_OP = 'INSERT' THEN\n            record_ids := ARRAY(\n                SELECT r.title_id FROM new_rows c JOIN public.title_results r ON r.id = c.result_id\n            );\n        ELSIF TG_OP = 'DELETE' THEN\n            record_ids := ARRAY(\n                SELECT r.title_id FROM old_rows c JOIN public.title_results r ON r.id = c.result_id\n            );\n        ELSE\n            record_ids := ARRAY(\n                SELECT r.title_id\n                FROM (SELECT result_id FROM new_rows UNION SELECT result_id FROM old_rows) c\n                JOIN public.title_results r ON r.id = c.result_id\n            );\n        END IF;\n    ELSIF TG_OP = 'DELETE' THEN\n        record_ids := ARRAY(SELECT public_id FROM old_rows);\n    ELSE\n        -- Records are updated by every recompute; only retirement and revival change their documents.\n        record_ids := ARRAY(\n            SELECT n.public_id\n            FROM new_rows n JOIN old_rows o ON o.public_id = n.public_id\n            WHERE n.retired_at IS DISTINCT FROM o.retired_at\n        );\n    END IF;\n    PERFORM public.upsert_search_projection_requests(\n        'record', ARRAY(SELECT 'record:' || id::text FROM unnest(record_ids) AS ids(id)), 'upsert'\n    );\n    RETURN NULL;\nEND;\n$$;",
)

_TRIGGERS = (
    "CREATE TRIGGER record_titles_enqueue_search_delete\n    AFTER DELETE ON public.record_titles\n    REFERENCING OLD TABLE AS old_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER record_titles_enqueue_search_update\n    AFTER UPDATE ON public.record_titles\n    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_holder_history_enqueue_search_delete\n    AFTER DELETE ON public.title_holder_history\n    REFERENCING OLD TABLE AS old_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_holder_history_enqueue_search_insert\n    AFTER INSERT ON public.title_holder_history\n    REFERENCING NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_holder_history_enqueue_search_update\n    AFTER UPDATE ON public.title_holder_history\n    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_holders_enqueue_search_delete\n    AFTER DELETE ON public.title_holders\n    REFERENCING OLD TABLE AS old_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_holders_enqueue_search_insert\n    AFTER INSERT ON public.title_holders\n    REFERENCING NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_holders_enqueue_search_update\n    AFTER UPDATE ON public.title_holders\n    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_results_enqueue_search_delete\n    AFTER DELETE ON public.title_results\n    REFERENCING OLD TABLE AS old_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_results_enqueue_search_insert\n    AFTER INSERT ON public.title_results\n    REFERENCING NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_results_enqueue_search_update\n    AFTER UPDATE ON public.title_results\n    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_tied_builds_enqueue_search_delete\n    AFTER DELETE ON public.title_tied_builds\n    REFERENCING OLD TABLE AS old_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_tied_builds_enqueue_search_insert\n    AFTER INSERT ON public.title_tied_builds\n    REFERENCING NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
    "CREATE TRIGGER title_tied_builds_enqueue_search_update\n    AFTER UPDATE ON public.title_tied_builds\n    REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows\n    FOR EACH STATEMENT\n    EXECUTE FUNCTION public.enqueue_record_search_projection();",
)

_OLD_FUNCTION = "CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_key text;\nBEGIN\n    IF TG_TABLE_NAME = 'title_results' THEN\n        target_key := 'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text;\n        -- One statement, so a worker committing the document and its acknowledgement\n        -- together cannot slip between the two checks.\n        IF TG_OP = 'DELETE' AND NOT EXISTS (\n            SELECT 1 FROM public.search_documents d\n            WHERE d.resource_kind = 'record' AND d.source_key = target_key\n            UNION ALL\n            SELECT 1 FROM public.search_projection_queue q\n            WHERE q.resource_kind = 'record' AND q.source_key = target_key\n        ) THEN\n            RETURN NULL;\n        END IF;\n        PERFORM public.upsert_search_projection_request(\n            'record', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END\n        );\n    ELSIF EXISTS (\n        SELECT 1 FROM public.title_results r\n        WHERE r.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END\n    ) THEN\n        PERFORM public.upsert_search_projection_request(\n            'record',\n            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,\n            'upsert'\n        );\n    END IF;\n    RETURN NULL;\nEND;\n$$;"

_OLD_TRIGGERS = (
    "CREATE TRIGGER title_results_enqueue_search\n    AFTER INSERT OR DELETE OR UPDATE ON public.title_results\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_computed_record_search_projection();",
    "CREATE TRIGGER title_holders_enqueue_search\n    AFTER INSERT OR DELETE OR UPDATE ON public.title_holders\n    FOR EACH ROW\n    EXECUTE FUNCTION public.enqueue_computed_record_search_projection();",
)

_COMMENTS = {
    "search_documents": "A searchable resource: what one search result shows, and the vector that finds it.\n\nDisplay columns and `document_data` render the hit. Every searchable fact is\na SearchValue row; `search_vector` and `heading_terms` are derived from those\nrows when the document is written, for fast recall and ranking.",
    "search_values": "One typed value of a document's field, such as one of a build's creators.\n\nText values keep their original text for display and highlighting, a\nnormalized form for keyword comparisons, and a vector of their terms for\nword and phrase matches scoped to the field. Record holder values carry the\nholder's build ID, so a clause can be matched against a single holder.",
    "search_terms": "A term that some document has contained, so queries can find close spellings of a word.\n\nRows are only added. A term no document still contains matches nothing, so it costs\nnothing but a row.",
    "search_embedding_queue": "A durable request to embed a search document whose embedding text changed.",
}

_REQUEUE = """
    INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
    SELECT 'build', id::text, 'upsert' FROM public.builds
    UNION ALL
    SELECT 'record', {record_key}, 'upsert' FROM public.{record_source}
    UNION ALL
    SELECT 'metadata', 'tag:' || id, 'upsert' FROM public.tag_definitions WHERE moderation_status = 'approved'
    UNION ALL
    SELECT 'metadata', 'creator:' || id, 'upsert' FROM public.creator_aliases
    UNION ALL
    SELECT 'metadata', 'version:' || id, 'upsert' FROM public.versions
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")
    for trigger in _OLD_TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(trigger))
    op.drop_entity(PGFunction.from_sql(_OLD_FUNCTION))

    op.execute("DELETE FROM public.search_projection_queue")
    op.drop_table("search_document_filters")
    op.execute("TRUNCATE public.search_documents CASCADE")
    for index in (
        "search_documents_title_fts_idx",
        "search_documents_description_fts_idx",
        "search_documents_combined_fts_idx",
        "search_documents_fuzzy_trgm_idx",
        "search_documents_tags_idx",
    ):
        op.drop_index(index, table_name="search_documents")
    for column in ("fuzzy_text", "tags", "title_vector", "description_vector", "combined_vector"):
        op.drop_column("search_documents", column)
    op.add_column(
        "search_documents",
        sa.Column(
            "heading_terms",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
            comment="Distinct sorted terms of the title and subtitle, to recognize a query that names the heading.",
        ),
    )
    op.add_column("search_documents", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False))
    op.add_column(
        "search_documents",
        sa.Column("embedding_text", sa.Text(), server_default=sa.text("''"), nullable=False),
    )
    op.add_column(
        "search_documents",
        sa.Column(
            "embedding_hash",
            sa.Text(),
            nullable=False,
            comment="Digest of embedding_text; the embedding is kept while it matches.",
        ),
    )
    op.alter_column(
        "search_documents",
        "normalized_title",
        comment="Normalized title and subtitle, for title order and suggestions.",
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "search_documents",
        "source_hash",
        comment="Digest of the whole document, to skip rewriting it unchanged.",
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    op.create_index("search_documents_search_vector_idx", "search_documents", ["search_vector"], postgresql_using="gin")
    op.create_index(
        "search_documents_title_trgm_idx",
        "search_documents",
        ["normalized_title"],
        postgresql_using="gin",
        postgresql_ops={"normalized_title": "gin_trgm_ops"},
    )

    op.create_table(
        "search_values",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("holder_build_id", sa.BigInteger(), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("term_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(), nullable=True),
        sa.Column("timestamp_value", sa.DateTime(timezone=True), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1",
            name="search_values_one_value_check",
        ),
        sa.CheckConstraint(
            "(text_value IS NULL) = (normalized_text IS NULL) AND (text_value IS NULL) = (term_vector IS NULL)",
            name="search_values_text_forms_check",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["search_documents.id"], name="search_values_document_id_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="search_values_pkey"),
        sa.UniqueConstraint("document_id", "field_name", "ordinal", name="search_values_identity_key"),
    )
    op.create_index(
        "search_values_holder_idx",
        "search_values",
        ["document_id", "holder_build_id", "field_name"],
        postgresql_where=sa.text("holder_build_id IS NOT NULL"),
    )
    op.create_index(
        "search_values_keyword_idx",
        "search_values",
        ["field_name", "normalized_text"],
        postgresql_where=sa.text("normalized_text IS NOT NULL"),
    )
    op.execute(
        "CREATE INDEX search_values_text_prefix_idx ON public.search_values "
        "(field_name, lower(text_value) text_pattern_ops) WHERE text_value IS NOT NULL"
    )
    op.create_index("search_values_term_vector_idx", "search_values", ["term_vector"], postgresql_using="gin")
    for name, column in (("numeric", "numeric_value"), ("timestamp", "timestamp_value"), ("boolean", "boolean_value")):
        op.create_index(
            f"search_values_{name}_idx",
            "search_values",
            ["field_name", column],
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )
    op.create_table(
        "search_terms",
        sa.Column("term", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("term", name="search_terms_pkey"),
    )

    for table, comment in _COMMENTS.items():
        op.create_table_comment(table, comment)

    for function in _FUNCTIONS:
        op.create_entity(PGFunction.from_sql(function))
    for trigger in _TRIGGERS:
        op.create_entity(PGTrigger.from_sql(trigger))

    op.execute(
        _REQUEUE.format(
            record_key="'record:' || t.public_id",
            record_source=(
                "record_titles t WHERE t.retired_at IS NOT NULL "
                "OR EXISTS (SELECT 1 FROM public.title_results r WHERE r.title_id = t.public_id)"
            ),
        )
    )


def downgrade() -> None:
    """Restore the shape of the text columns; the index is rebuilt with the old keys."""
    for trigger in _TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(trigger))
    for function in reversed(_FUNCTIONS):
        op.drop_entity(PGFunction.from_sql(function))

    op.execute("DELETE FROM public.search_projection_queue")
    op.drop_table("search_terms")
    op.drop_table("search_values")
    op.execute("TRUNCATE public.search_documents CASCADE")
    op.drop_index("search_documents_title_trgm_idx", table_name="search_documents")
    op.drop_index("search_documents_search_vector_idx", table_name="search_documents")
    for column in ("heading_terms", "search_vector", "embedding_text", "embedding_hash"):
        op.drop_column("search_documents", column)
    op.alter_column(
        "search_documents", "normalized_title", comment=None, existing_type=sa.Text(), existing_nullable=False
    )
    op.alter_column("search_documents", "source_hash", comment=None, existing_type=sa.Text(), existing_nullable=False)
    op.add_column("search_documents", sa.Column("fuzzy_text", sa.Text(), nullable=False))
    op.add_column(
        "search_documents",
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False),
    )
    for column in ("title_vector", "description_vector", "combined_vector"):
        op.add_column("search_documents", sa.Column(column, postgresql.TSVECTOR(), nullable=True))
    for index, column in (
        ("search_documents_title_fts_idx", "title_vector"),
        ("search_documents_description_fts_idx", "description_vector"),
        ("search_documents_combined_fts_idx", "combined_vector"),
        ("search_documents_tags_idx", "tags"),
    ):
        op.create_index(index, "search_documents", [column], postgresql_using="gin")
    op.create_index(
        "search_documents_fuzzy_trgm_idx",
        "search_documents",
        ["fuzzy_text"],
        postgresql_using="gin",
        postgresql_ops={"fuzzy_text": "gin_trgm_ops"},
    )

    op.create_table(
        "search_document_filters",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("holder_build_id", sa.BigInteger(), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(), nullable=True),
        sa.Column("timestamp_value", sa.DateTime(timezone=True), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(text_value, numeric_value, timestamp_value, boolean_value) = 1",
            name="search_document_filters_one_value_check",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["search_documents.id"],
            name="search_document_filters_document_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="search_document_filters_pkey"),
        sa.UniqueConstraint("document_id", "field_name", "ordinal", name="search_document_filters_identity_key"),
    )
    op.create_index(
        "search_document_filters_holder_idx",
        "search_document_filters",
        ["document_id", "holder_build_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("holder_build_id IS NOT NULL"),
    )
    op.create_index(
        "search_document_filters_text_idx",
        "search_document_filters",
        ["field_name", "text_value"],
        postgresql_where=sa.text("text_value IS NOT NULL"),
    )
    op.execute(
        "CREATE INDEX search_document_filters_text_prefix_idx ON public.search_document_filters "
        "(field_name, lower(text_value) text_pattern_ops) WHERE text_value IS NOT NULL"
    )
    for name, column in (("numeric", "numeric_value"), ("timestamp", "timestamp_value"), ("boolean", "boolean_value")):
        op.create_index(
            f"search_document_filters_{name}_idx",
            "search_document_filters",
            ["field_name", column],
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )

    op.create_table_comment("search_documents", "An indexed projection of a searchable application resource.")
    op.create_table_comment(
        "search_embedding_queue", "A durable request to embed a search document whose source hash changed."
    )
    op.create_entity(PGFunction.from_sql(_OLD_FUNCTION))
    for trigger in _OLD_TRIGGERS:
        op.create_entity(PGTrigger.from_sql(trigger))
    op.execute(_REQUEUE.format(record_key="'result:' || r.id", record_source="title_results r"))
