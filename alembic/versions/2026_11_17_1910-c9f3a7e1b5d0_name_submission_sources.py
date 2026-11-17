"""Name build and tag source data explicitly without changing attribution."""

from alembic import op

revision = "c9f3a7e1b5d0"
down_revision = "b8e2c6f0a4d9"
branch_labels = None
depends_on = None

_SNAPSHOT_SQL = "CREATE OR REPLACE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb\n    LANGUAGE sql STABLE\n    SET timezone = 'UTC'\n    AS $$\nSELECT jsonb_build_object(\n    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),\n    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'expansion', d.expansion, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),\n    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type, 'extension_time', d.extension_time, 'retraction_time', d.retraction_time) FROM public.extenders d WHERE d.build_id = b.id),\n    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_credits c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),\n    'credits', COALESCE((SELECT jsonb_agg(jsonb_build_object('kind', c.kind, 'name', c.name, 'username', c.username, 'subject', c.subject, 'role', c.role, 'attribution', c.attribution, 'source_message_id', c.source_message_id, 'alias_id', c.alias_id) ORDER BY c.position) FROM public.build_credits c WHERE c.build_id = b.id), '[]'::jsonb),\n    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),\n    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'origin', r.origin, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)\n) FROM public.builds b WHERE b.id = target_build_id;\n$$;\n"

_REWRITE = """
CREATE FUNCTION pg_temp.rewrite_submission_sources(value jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    result jsonb;
    name text;
    item jsonb;
    renamed text;
BEGIN
    IF jsonb_typeof(value) = 'array' THEN
        SELECT COALESCE(jsonb_agg(pg_temp.rewrite_submission_sources(element) ORDER BY position), '[]'::jsonb)
        INTO result FROM jsonb_array_elements(value) WITH ORDINALITY AS items(element, position);
        RETURN result;
    ELSIF jsonb_typeof(value) <> 'object' THEN
        RETURN value;
    END IF;
    result := '{}'::jsonb;
    FOR name, item IN SELECT * FROM jsonb_each(value) LOOP
        renamed := CASE name WHEN 'provenance' THEN 'origin'
                             WHEN 'submission_provenance' THEN 'source_submission' ELSE name END;
        IF renamed <> name AND value ? renamed THEN
            RAISE EXCEPTION 'Conflicting source keys in retained build data';
        END IF;
        result := result || jsonb_build_object(renamed, pg_temp.rewrite_submission_sources(item));
    END LOOP;
    RETURN result;
END;
$$;
"""


def upgrade() -> None:
    op.alter_column("build_tag_assignments", "provenance", new_column_name="origin")
    op.execute(
        "ALTER TABLE build_tag_assignments RENAME CONSTRAINT "
        "build_tag_assignments_provenance_check TO build_tag_assignments_origin_check"
    )
    op.execute(_SNAPSHOT_SQL)
    op.execute(_REWRITE)
    # This changes serialization names, not build revisions or historical facts.
    for table, columns in [
        ("builds", ("extra_info",)),
        ("build_history_states", ("snapshot",)),
        ("build_edit_pending", ("before_snapshot",)),
        ("submission_inference_runs", ("candidates",)),
    ]:
        op.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        for column in columns:
            op.execute(
                f"UPDATE {table} SET {column} = pg_temp.rewrite_submission_sources({column}) "
                f"WHERE {column}::text LIKE '%provenance%'"
            )
        op.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def downgrade() -> None:
    message = "Source names were rewritten in retained history; restore a database backup"
    raise RuntimeError(message)
