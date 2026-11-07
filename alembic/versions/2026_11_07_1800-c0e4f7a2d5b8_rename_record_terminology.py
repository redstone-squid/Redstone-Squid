"""Align record identities, persisted filters, and projections with the rules vocabulary."""

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision: str = "c0e4f7a2d5b8"
down_revision: str | None = "b9d3e6f1c4a7"
branch_labels: str | None = None
depends_on: str | None = None

_TABLES = (
    ("record_rulesets", "rules_revisions"),
    ("record_competitions", "record_titles"),
    ("record_definitions", "title_definitions"),
    ("record_definition_facets", "title_definition_facets"),
    ("record_results", "title_results"),
    ("record_result_holders", "title_holders"),
    ("record_holder_history", "title_holder_history"),
)
_CONSTRAINTS = (
    ("rules_revisions", "record_rulesets_content_key", "rules_revisions_content_key"),
    ("rules_revisions", "record_rulesets_pkey", "rules_revisions_pkey"),
    (
        "title_definitions",
        "record_definitions_materialization_source_check",
        "title_definitions_materialization_source_check",
    ),
    ("title_definitions", "record_definitions_identity_key", "title_definitions_identity_key"),
    ("title_definitions", "record_definitions_competition_id_fkey", "title_definitions_title_id_fkey"),
    ("title_definitions", "record_definitions_ruleset_id_fkey", "title_definitions_rules_revision_id_fkey"),
    ("title_definitions", "record_definitions_pkey", "title_definitions_pkey"),
    ("record_titles", "record_competitions_record_class_check", "record_titles_base_title_check"),
    ("record_titles", "record_competitions_version_scope_check", "record_titles_version_scope_check"),
    ("record_titles", "record_competitions_build_kind_check", "record_titles_build_kind_check"),
    ("record_titles", "record_competitions_identity_key", "record_titles_identity_key"),
    ("record_titles", "record_competitions_version_id_fkey", "record_titles_version_id_fkey"),
    ("record_titles", "record_competitions_pkey", "record_titles_pkey"),
    ("title_definition_facets", "record_definition_facets_kind_check", "title_definition_facets_kind_check"),
    (
        "title_definition_facets",
        "record_definition_facets_definition_id_fkey",
        "title_definition_facets_definition_id_fkey",
    ),
    ("title_definition_facets", "record_definition_facets_pkey", "title_definition_facets_pkey"),
    (
        "record_computation_runs",
        "record_computation_runs_ruleset_id_fkey",
        "record_computation_runs_rules_revision_id_fkey",
    ),
    ("title_results", "record_results_status_check", "title_results_status_check"),
    ("title_results", "record_results_run_definition_key", "title_results_run_definition_key"),
    ("title_results", "record_results_run_id_fkey", "title_results_run_id_fkey"),
    ("title_results", "record_results_definition_id_fkey", "title_results_definition_id_fkey"),
    ("title_results", "record_results_provisional_build_id_fkey", "title_results_provisional_build_id_fkey"),
    ("title_results", "record_results_pkey", "title_results_pkey"),
    ("title_holders", "record_result_holders_rank_check", "title_holders_rank_check"),
    ("title_holders", "record_result_holders_result_id_fkey", "title_holders_result_id_fkey"),
    ("title_holders", "record_result_holders_build_id_fkey", "title_holders_build_id_fkey"),
    ("title_holders", "record_result_holders_pkey", "title_holders_pkey"),
    ("title_holder_history", "record_holder_history_interval_check", "title_holder_history_interval_check"),
    ("title_holder_history", "record_holder_history_identity_key", "title_holder_history_identity_key"),
    ("title_holder_history", "record_holder_history_run_id_fkey", "title_holder_history_run_id_fkey"),
    ("title_holder_history", "record_holder_history_definition_id_fkey", "title_holder_history_definition_id_fkey"),
    ("title_holder_history", "record_holder_history_build_id_fkey", "title_holder_history_build_id_fkey"),
    ("title_holder_history", "record_holder_history_predecessor_id_fkey", "title_holder_history_predecessor_id_fkey"),
    ("title_holder_history", "record_holder_history_pkey", "title_holder_history_pkey"),
)
_INDEXES = (
    ("record_definitions_competition_idx", "title_definitions_title_idx"),
    ("record_competitions_category_idx", "record_titles_category_idx"),
    ("record_definition_facets_lookup_idx", "title_definition_facets_lookup_idx"),
    ("record_results_provisional_build_idx", "title_results_provisional_build_idx"),
    ("record_results_definition_idx", "title_results_definition_idx"),
    ("record_result_holders_build_idx", "title_holders_build_idx"),
    ("record_holder_history_build_idx", "title_holder_history_build_idx"),
    ("record_holder_history_definition_idx", "title_holder_history_definition_idx"),
)
_SEQUENCES = (
    ("record_rulesets_id_seq", "rules_revisions_id_seq"),
    ("record_definitions_id_seq", "title_definitions_id_seq"),
    ("record_results_id_seq", "title_results_id_seq"),
    ("record_holder_history_id_seq", "title_holder_history_id_seq"),
)
_FUNCTION_SQL = """CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_result_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'title_results' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        VALUES (
            'record',
            'result:' || target_result_id::text,
            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
            now(),
            now()
        )
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSIF TG_TABLE_NAME = 'title_holders' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END;
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        VALUES ('record', 'result:' || target_result_id::text, 'upsert', now(), now())
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    ELSE
        INSERT INTO public.search_projection_queue
            (resource_kind, source_key, action, enqueued_at, available_at)
        SELECT 'record', 'result:' || rr.id::text, 'upsert', now(), now()
        FROM public.title_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at,
            available_at = EXCLUDED.available_at,
            attempts = 0, claimed_at = NULL, claim_token = NULL, dead_at = NULL, last_error = NULL;
    END IF;
    RETURN NULL;
END;
$$;"""
_TRIGGER_SQL = (
    "CREATE TRIGGER title_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();",
    "CREATE TRIGGER title_holders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.title_holders FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();",
)


def _old_sql(sql: str) -> str:
    for old, new in _TABLES:
        sql = sql.replace(new, old)
    return sql


def _rewrite_json(old_key: str, new_key: str, old_scope: str, new_scope: str) -> None:
    # Preserve unrelated keys and the order of version scopes in subscription filters.
    op.execute(f"""
        UPDATE notification_subscriptions SET filter = (filter - '{old_key}') ||
            jsonb_build_object('{new_key}', filter->'{old_key}') WHERE filter ? '{old_key}'
    """)
    op.execute(f"""
        UPDATE notification_subscriptions SET filter = jsonb_set(filter, '{{version_scopes}}',
            (SELECT jsonb_agg(CASE WHEN value = '"{old_scope}"'::jsonb
                THEN '"{new_scope}"'::jsonb ELSE value END ORDER BY ordinal)
             FROM jsonb_array_elements(filter->'version_scopes') WITH ORDINALITY AS scopes(value, ordinal)))
        WHERE filter->'version_scopes' @> '["{old_scope}"]'::jsonb
    """)
    op.execute(f"""
        UPDATE search_documents SET tags = array_replace(tags, '{old_scope}', '{new_scope}')
        WHERE resource_kind = 'record'
    """)
    field_old, field_new = old_key.removesuffix("s"), new_key.removesuffix("s")
    # record_classes is not a regular plural.
    field_old = "record_class" if old_key == "record_classes" else field_old
    field_new = "record_class" if new_key == "record_classes" else field_new
    op.execute(f"""
        UPDATE search_documents SET document_data = (document_data - '{field_old}') ||
            jsonb_build_object('{field_new}', document_data->'{field_old}')
        WHERE resource_kind = 'record' AND document_data ? '{field_old}'
    """)
    op.execute(f"""
        UPDATE search_documents SET document_data = jsonb_set(document_data, '{{version_scope}}',
            '"{new_scope}"'::jsonb)
        WHERE resource_kind = 'record' AND document_data->>'version_scope' = '{old_scope}'
    """)
    op.execute(f"""
        UPDATE search_document_facets f SET field_name = '{field_new}'
        FROM search_documents d WHERE d.id = f.document_id AND d.resource_kind = 'record'
            AND f.field_name = '{field_old}'
    """)
    op.execute(f"""
        UPDATE search_document_facets f SET text_value = '{new_scope}'
        FROM search_documents d WHERE d.id = f.document_id AND d.resource_kind = 'record'
            AND f.field_name = 'version_scope' AND f.text_value = '{old_scope}'
    """)


def _rename_objects(reverse: bool = False) -> None:
    for table, old, new in _CONSTRAINTS:
        source, target = (new, old) if reverse else (old, new)
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{source}" TO "{target}"')
    for kind, pairs in (("INDEX", _INDEXES), ("SEQUENCE", _SEQUENCES)):
        for old, new in pairs:
            source, target = (new, old) if reverse else (old, new)
            op.execute(f'ALTER {kind} "{source}" RENAME TO "{target}"')


def upgrade() -> None:
    for sql in _TRIGGER_SQL:
        op.drop_entity(PGTrigger.from_sql(_old_sql(sql)))
    for old, new in _TABLES:
        op.rename_table(old, new)
    for table, old, new in (
        ("record_titles", "record_class", "base_title"),
        ("title_definitions", "competition_id", "title_id"),
        ("title_definitions", "ruleset_id", "rules_revision_id"),
        ("record_computation_runs", "ruleset_id", "rules_revision_id"),
    ):
        op.alter_column(table, old, new_column_name=new)
    _rename_objects()
    op.create_table_comment("record_titles", "A stable public identity for a record title across rules revisions.")
    op.create_table_comment("title_definitions", "The wording of one stable record title under a rules revision.")
    op.drop_constraint("record_titles_version_scope_check", "record_titles", type_="check")
    op.execute("UPDATE record_titles SET version_scope = 'up_to_date' WHERE version_scope = 'current'")
    op.create_check_constraint(
        "record_titles_version_scope_check", "record_titles", "version_scope IN ('all_time', 'up_to_date')"
    )
    _rewrite_json("record_classes", "base_titles", "current", "up_to_date")
    op.replace_entity(PGFunction.from_sql(_FUNCTION_SQL))
    for sql in _TRIGGER_SQL:
        op.create_entity(PGTrigger.from_sql(sql))


def downgrade() -> None:
    for sql in _TRIGGER_SQL:
        op.drop_entity(PGTrigger.from_sql(sql))
    _rewrite_json("base_titles", "record_classes", "up_to_date", "current")
    op.drop_constraint("record_titles_version_scope_check", "record_titles", type_="check")
    op.execute("UPDATE record_titles SET version_scope = 'current' WHERE version_scope = 'up_to_date'")
    op.create_check_constraint(
        "record_titles_version_scope_check", "record_titles", "version_scope IN ('all_time', 'current')"
    )
    op.create_table_comment(
        "record_titles", "A stable public identity for a logical record competition across rulesets."
    )
    op.create_table_comment("title_definitions", "A ruleset-specific definition of one stable record competition.")
    _rename_objects(reverse=True)
    for table, old, new in (
        ("record_titles", "base_title", "record_class"),
        ("title_definitions", "title_id", "competition_id"),
        ("title_definitions", "rules_revision_id", "ruleset_id"),
        ("record_computation_runs", "rules_revision_id", "ruleset_id"),
    ):
        op.alter_column(table, old, new_column_name=new)
    for old, new in reversed(_TABLES):
        op.rename_table(new, old)
    op.replace_entity(PGFunction.from_sql(_old_sql(_FUNCTION_SQL)))
    for sql in _TRIGGER_SQL:
        op.create_entity(PGTrigger.from_sql(_old_sql(sql)))
