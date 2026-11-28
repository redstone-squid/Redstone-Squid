"""Snapshot name ownership onto individual build credits."""

import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "c6a923d9bc70"
down_revision = "b67a02f1de93"
branch_labels = None
depends_on = None


_OLD = """CREATE OR REPLACE FUNCTION public.build_credit_accounts(target_build_id bigint DEFAULT NULL)
RETURNS TABLE(build_id bigint, "position" smallint, account_id integer, name text, role text)
LANGUAGE sql STABLE AS $$
    SELECT c.build_id, c.position, coalesce(i.account_id, a.account_id), c.name, c.role
    FROM public.build_credits c
    LEFT JOIN public.creator_aliases a ON a.id = c.alias_id
    LEFT JOIN public.account_identities i ON i.subject = c.subject AND i.provider = CASE c.kind
        WHEN 'discord' THEN 'discord'
        WHEN 'minecraft_java' THEN 'java'
        WHEN 'minecraft_bedrock' THEN 'bedrock'
    END
    WHERE (target_build_id IS NULL OR c.build_id = target_build_id)
      AND coalesce(i.account_id, a.account_id) IS NOT NULL;
$$;"""
_NEW = """CREATE OR REPLACE FUNCTION public.build_credit_accounts(target_build_id bigint DEFAULT NULL)
RETURNS TABLE(build_id bigint, "position" smallint, account_id integer, name text, role text)
LANGUAGE sql STABLE AS $$
    SELECT c.build_id, c.position, coalesce(c.account_id, i.account_id), c.name, c.role
    FROM public.build_credits c
    LEFT JOIN public.account_identities i ON c.account_source = 'automatic' AND i.subject = c.subject AND i.provider = CASE c.kind
        WHEN 'discord' THEN 'discord'
        WHEN 'minecraft_java' THEN 'java'
        WHEN 'minecraft_bedrock' THEN 'bedrock'
    END
    WHERE (target_build_id IS NULL OR c.build_id = target_build_id)
      AND coalesce(c.account_id, i.account_id) IS NOT NULL;
$$;"""


_SNAPSHOT_OLD = "CREATE OR REPLACE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb\n    LANGUAGE sql STABLE\n    SET timezone = 'UTC'\n    AS $$\nSELECT jsonb_build_object(\n    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'server_address', b.server_address, 'server_coordinates', b.server_coordinates, 'server_command', b.server_command, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),\n    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'expansion', d.expansion, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),\n    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type, 'extension_time', d.extension_time, 'retraction_time', d.retraction_time) FROM public.extenders d WHERE d.build_id = b.id),\n    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_credits c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),\n    'credits', COALESCE((SELECT jsonb_agg(jsonb_build_object('kind', c.kind, 'name', c.name, 'username', c.username, 'subject', c.subject, 'role', c.role, 'attribution', c.attribution, 'source_message_id', c.source_message_id, 'alias_id', c.alias_id) ORDER BY c.position) FROM public.build_credits c WHERE c.build_id = b.id), '[]'::jsonb),\n    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),\n    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'origin', r.origin, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)\n) || jsonb_strip_nulls(jsonb_build_object(\n    'door_timing_variants', (\n        SELECT jsonb_agg(to_jsonb(v) - 'build_id' ORDER BY v.id)\n        FROM public.door_timing_variants v WHERE v.build_id = b.id\n    ),\n    'extender_timing_variants', (\n        SELECT jsonb_agg(to_jsonb(v) - 'build_id' ORDER BY v.id)\n        FROM public.extender_timing_variants v WHERE v.build_id = b.id\n    )\n)) FROM public.builds b WHERE b.id = target_build_id;\n$$;\n"
_SNAPSHOT_NEW = "CREATE OR REPLACE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb\n    LANGUAGE sql STABLE\n    SET timezone = 'UTC'\n    AS $$\nSELECT jsonb_build_object(\n    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'server_address', b.server_address, 'server_coordinates', b.server_coordinates, 'server_command', b.server_command, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),\n    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'expansion', d.expansion, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),\n    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type, 'extension_time', d.extension_time, 'retraction_time', d.retraction_time) FROM public.extenders d WHERE d.build_id = b.id),\n    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', c.account_id) ORDER BY a.id) FROM public.build_credits c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),\n    'credits', COALESCE((SELECT jsonb_agg(jsonb_build_object('credit_id', c.id, 'account_id', c.account_id, 'account_source', c.account_source, 'proposed_account_id', c.proposed_account_id, 'kind', c.kind, 'name', c.name, 'username', c.username, 'subject', c.subject, 'role', c.role, 'attribution', c.attribution, 'source_message_id', c.source_message_id, 'alias_id', c.alias_id) ORDER BY c.position) FROM public.build_credits c WHERE c.build_id = b.id), '[]'::jsonb),\n    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),\n    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'origin', r.origin, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),\n    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)\n) || jsonb_strip_nulls(jsonb_build_object(\n    'door_timing_variants', (\n        SELECT jsonb_agg(to_jsonb(v) - 'build_id' ORDER BY v.id)\n        FROM public.door_timing_variants v WHERE v.build_id = b.id\n    ),\n    'extender_timing_variants', (\n        SELECT jsonb_agg(to_jsonb(v) - 'build_id' ORDER BY v.id)\n        FROM public.extender_timing_variants v WHERE v.build_id = b.id\n    )\n)) FROM public.builds b WHERE b.id = target_build_id;\n$$;\n"

_ENQUEUE_OLD = "-- Queue the work that follows a change to a build or to one of the tables that describe it:\n-- refresh its search document, recompute the records it affects, and sync its Discord posts.\nCREATE OR REPLACE FUNCTION public.enqueue_build_change() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_build_id bigint;\n    target_kind text;\n    previous_kind text;\n    build_exists boolean := true;\n    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';\nBEGIN\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);\n        IF TG_OP = 'UPDATE' THEN\n            previous_kind := lower(OLD.category);\n        END IF;\n    ELSE\n        -- When the row is going away with its build, the build's own trigger queues the deletion.\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;\n        build_exists := FOUND;\n    END IF;\n\n    -- A link change queues only the Discord sync.\n    IF TG_TABLE_NAME <> 'build_links' THEN\n        PERFORM public.upsert_search_projection_request(\n            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END\n        );\n\n        IF target_kind IN ('door', 'extender') OR previous_kind IN ('door', 'extender') THEN\n            -- One item per build and kind; a build moving between kinds affects both. Deleted builds\n            -- keep their ID, so the worker can still find the records they leave. As in the\n            -- upsert_*_request functions, clearing the claim token keeps an older worker from\n            -- acknowledging the new request.\n            INSERT INTO public.record_recompute_queue\n                (scope_key, build_kind, target, build_id, reasons, enqueued_at, available_at)\n            SELECT kind || ':build:' || target_build_id, kind, 'build', target_build_id,\n                '[\"source_change\"]'::jsonb, now(), now()\n            FROM (\n                SELECT DISTINCT kind FROM unnest(ARRAY[target_kind, previous_kind]) AS kinds(kind)\n                WHERE kind IN ('door', 'extender')\n            ) AS affected\n            ORDER BY kind\n            ON CONFLICT (scope_key) DO UPDATE\n            SET enqueued_at = EXCLUDED.enqueued_at,\n                available_at = EXCLUDED.available_at,\n                attempts = 0,\n                claimed_at = NULL,\n                claim_token = NULL,\n                last_error = NULL;\n        END IF;\n    END IF;\n\n    IF build_deleted THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');\n    ELSIF build_exists THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');\n\n        -- A review card embeds the build it is voting on, so a build change makes the\n        -- session's cards stale too. Their posts are keyed by session, not by build, so\n        -- the sessions have to be enqueued in their own right.\n        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')\n        FROM public.build_vote_sessions bvs\n        WHERE bvs.build_id = target_build_id;\n    END IF;\n    RETURN NULL;\nEND;\n$$;\n"
_ENQUEUE_NEW = "-- Queue the work that follows a change to a build or to one of the tables that describe it:\n-- refresh its search document, recompute the records it affects, and sync its Discord posts.\nCREATE OR REPLACE FUNCTION public.enqueue_build_change() RETURNS trigger\n    LANGUAGE plpgsql\n    AS $$\nDECLARE\n    target_build_id bigint;\n    target_kind text;\n    previous_kind text;\n    build_exists boolean := true;\n    build_deleted boolean := TG_TABLE_NAME = 'builds' AND TG_OP = 'DELETE';\nBEGIN\n    IF TG_TABLE_NAME = 'builds' THEN\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;\n        target_kind := lower(CASE WHEN TG_OP = 'DELETE' THEN OLD.category ELSE NEW.category END);\n        IF TG_OP = 'UPDATE' THEN\n            previous_kind := lower(OLD.category);\n        END IF;\n    ELSE\n        -- When the row is going away with its build, the build's own trigger queues the deletion.\n        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;\n        SELECT lower(category) INTO target_kind FROM public.builds WHERE id = target_build_id;\n        build_exists := FOUND;\n    END IF;\n\n    IF TG_TABLE_NAME = 'build_credits' THEN\n        IF TG_OP <> 'DELETE' AND NEW.alias_id IS NOT NULL THEN\n            PERFORM public.upsert_search_projection_request('metadata', 'creator:' || NEW.alias_id::text, 'upsert');\n        END IF;\n        IF TG_OP <> 'INSERT' AND OLD.alias_id IS NOT NULL THEN\n            PERFORM public.upsert_search_projection_request('metadata', 'creator:' || OLD.alias_id::text, 'upsert');\n        END IF;\n    ELSIF TG_TABLE_NAME = 'builds' AND TG_OP = 'UPDATE' THEN\n        IF NEW.submission_status IS DISTINCT FROM OLD.submission_status THEN\n            PERFORM public.upsert_search_projection_request('metadata', 'creator:' || c.alias_id::text, 'upsert')\n            FROM (SELECT DISTINCT alias_id FROM public.build_credits WHERE build_id = NEW.id AND alias_id IS NOT NULL ORDER BY alias_id) c;\n        END IF;\n    END IF;\n\n    -- A link change queues only the Discord sync.\n    IF TG_TABLE_NAME <> 'build_links' THEN\n        PERFORM public.upsert_search_projection_request(\n            'build', target_build_id::text, CASE WHEN build_deleted THEN 'delete' ELSE 'upsert' END\n        );\n\n        IF target_kind IN ('door', 'extender') OR previous_kind IN ('door', 'extender') THEN\n            -- One item per build and kind; a build moving between kinds affects both. Deleted builds\n            -- keep their ID, so the worker can still find the records they leave. As in the\n            -- upsert_*_request functions, clearing the claim token keeps an older worker from\n            -- acknowledging the new request.\n            INSERT INTO public.record_recompute_queue\n                (scope_key, build_kind, target, build_id, reasons, enqueued_at, available_at)\n            SELECT kind || ':build:' || target_build_id, kind, 'build', target_build_id,\n                '[\"source_change\"]'::jsonb, now(), now()\n            FROM (\n                SELECT DISTINCT kind FROM unnest(ARRAY[target_kind, previous_kind]) AS kinds(kind)\n                WHERE kind IN ('door', 'extender')\n            ) AS affected\n            ORDER BY kind\n            ON CONFLICT (scope_key) DO UPDATE\n            SET enqueued_at = EXCLUDED.enqueued_at,\n                available_at = EXCLUDED.available_at,\n                attempts = 0,\n                claimed_at = NULL,\n                claim_token = NULL,\n                last_error = NULL;\n        END IF;\n    END IF;\n\n    IF build_deleted THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'delete');\n    ELSIF build_exists THEN\n        PERFORM public.upsert_discord_sync_request('build', target_build_id::text, 'refresh');\n\n        -- A review card embeds the build it is voting on, so a build change makes the\n        -- session's cards stale too. Their posts are keyed by session, not by build, so\n        -- the sessions have to be enqueued in their own right.\n        PERFORM public.upsert_discord_sync_request('vote_session', bvs.vote_session_id::text, 'refresh')\n        FROM public.build_vote_sessions bvs\n        WHERE bvs.build_id = target_build_id;\n    END IF;\n    RETURN NULL;\nEND;\n$$;\n"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE build_credits
        ADD COLUMN account_id integer REFERENCES accounts(id) ON DELETE SET NULL,
        ADD COLUMN proposed_account_id integer REFERENCES accounts(id) ON DELETE SET NULL,
        ADD COLUMN account_source text NOT NULL DEFAULT 'automatic'
            CONSTRAINT build_credits_account_source_check CHECK (account_source IN ('automatic','reviewed','conflict'))
    """)
    op.create_index("ix_build_credits_account_id", "build_credits", ["account_id"])
    op.create_index("ix_build_credits_proposed_account_id", "build_credits", ["proposed_account_id"])
    op.execute("ALTER TABLE build_credits DISABLE TRIGGER build_credits_capture_edit")
    op.execute("""
        WITH owners AS (
            SELECT c.id, a.account_id,
                EXISTS (SELECT FROM account_identities i WHERE i.subject = c.subject
                    AND i.provider = CASE c.kind WHEN 'discord' THEN 'discord' WHEN 'minecraft_java' THEN 'java'
                    WHEN 'minecraft_bedrock' THEN 'bedrock' END AND i.account_id <> a.account_id) AS conflict
            FROM build_credits c JOIN creator_aliases a ON a.id = c.alias_id WHERE a.account_id IS NOT NULL
        )
        UPDATE build_credits c SET account_id = CASE WHEN NOT owners.conflict THEN owners.account_id END,
            account_source = CASE WHEN owners.conflict THEN 'conflict' ELSE 'automatic' END
        FROM owners WHERE c.id = owners.id
    """)
    op.execute("""DO $$ DECLARE credit uuid; BEGIN
        FOR credit IN SELECT id FROM build_credits WHERE account_source = 'conflict' LOOP
            RAISE WARNING 'Conflicting account attribution on credit %', credit;
        END LOOP;
    END $$""")
    bind = op.get_bind()
    names = (
        bind.execute(sa.text("SELECT DISTINCT name FROM build_credits WHERE alias_id IS NULL ORDER BY name"))
        .scalars()
        .all()
    )
    if names:
        op.execute("CREATE TEMP TABLE credit_name_folds(name text PRIMARY KEY, folded text NOT NULL) ON COMMIT DROP")
        bind.execute(
            sa.text("INSERT INTO credit_name_folds(name, folded) VALUES (:name, :folded)"),
            [{"name": name, "folded": unicodedata.normalize("NFKC", name).strip().casefold()} for name in names],
        )
        op.execute("""INSERT INTO creator_aliases(name, normalized_name)
                      SELECT min(name), folded FROM credit_name_folds GROUP BY folded
                      ON CONFLICT (normalized_name) DO NOTHING""")
        op.execute("""UPDATE build_credits c SET alias_id = a.id FROM credit_name_folds f
                      JOIN creator_aliases a ON a.normalized_name = f.folded
                      WHERE c.name = f.name AND c.alias_id IS NULL""")
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("ALTER TABLE build_credits ENABLE TRIGGER build_credits_capture_edit")
    op.execute(
        "ALTER TABLE creator_alias_claims ADD COLUMN selected_credits jsonb NOT NULL DEFAULT '{}', ADD COLUMN approved_credit_ids jsonb NOT NULL DEFAULT '[]'"
    )
    op.execute(
        "COMMENT ON COLUMN creator_alias_claims.selected_credits IS 'Credit IDs selected at filing and the effective owners staff will review.'"
    )
    op.drop_index("creator_alias_claims_one_pending_per_account", table_name="creator_alias_claims")
    op.execute(_SNAPSHOT_NEW)
    op.execute(_ENQUEUE_NEW)
    op.execute(_NEW)
    op.execute(
        "SELECT public.upsert_search_projection_request('metadata', 'creator:' || id::text, 'upsert') FROM creator_aliases ORDER BY id"
    )


def downgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX creator_alias_claims_one_pending_per_account ON creator_alias_claims(alias_id, account_id) WHERE status = 'pending'"
    )
    op.execute("ALTER TABLE creator_alias_claims DROP COLUMN selected_credits, DROP COLUMN approved_credit_ids")
    op.execute(_SNAPSHOT_OLD)
    op.execute(_ENQUEUE_OLD)
    op.execute(_OLD)
    op.execute(
        "ALTER TABLE build_credits DROP COLUMN proposed_account_id, DROP COLUMN account_id, DROP COLUMN account_source"
    )
