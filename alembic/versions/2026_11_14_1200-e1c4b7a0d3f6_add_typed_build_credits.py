"""Replace name-only build creators with typed credits while preserving alias claims.

Revision ID: e1c4b7a0d3f6
Revises: d89ea6c7c875
"""

import sqlalchemy as sa
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision = "e1c4b7a0d3f6"
down_revision = "d89ea6c7c875"
branch_labels = None
depends_on = None

_OLD_FUNCTIONS = [
    """CREATE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb
    LANGUAGE sql STABLE
    SET timezone = 'UTC'
    AS $$
SELECT jsonb_build_object(
    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),
    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),
    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type, 'extension_time', d.extension_time, 'retraction_time', d.retraction_time) FROM public.extenders d WHERE d.build_id = b.id),
    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_creators c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),
    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),
    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'provenance', r.provenance, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),
    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),
    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),
    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)
) FROM public.builds b WHERE b.id = target_build_id;
$$;
""",
    """CREATE FUNCTION public.capture_build_edit_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    old_value jsonb;
    new_value jsonb;
    ignored text[] := ARRAY[]::text[];
    build_ids bigint[];
    target bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'Use DELETE inside a build editing scope instead of TRUNCATE' USING ERRCODE = '55000';
    END IF;
    old_value := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END;
    new_value := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END;
    IF TG_TABLE_NAME = 'builds' THEN
        ignored := ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at', 'revision', 'edited_time'];
    ELSIF TG_TABLE_NAME = 'build_tag_assignments' THEN
        ignored := ARRAY['created_at', 'updated_at'];
    ELSIF TG_TABLE_NAME = 'build_schematics' THEN
        -- The history contract retains attachment/publication metadata, not recomputable analyses.
        SELECT jsonb_object_agg(key, value) INTO old_value FROM jsonb_each(old_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
        SELECT jsonb_object_agg(key, value) INTO new_value FROM jsonb_each(new_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        ignored := ARRAY['normalized_name','claimed_at','claim_method','created_at'];
    END IF;
    IF (old_value - ignored) IS NOT DISTINCT FROM (new_value - ignored) THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_TABLE_NAME = 'creator_aliases' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_creators
        WHERE alias_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'versions' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_versions
        WHERE version_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'builds' THEN
        build_ids := ARRAY[(old_value->>'id')::bigint, (new_value->>'id')::bigint];
    ELSE
        build_ids := ARRAY[(old_value->>'build_id')::bigint, (new_value->>'build_id')::bigint];
    END IF;
    FOR target IN SELECT DISTINCT value FROM unnest(build_ids) AS value WHERE value IS NOT NULL ORDER BY value LOOP
        PERFORM public.capture_build_edit(target);
    END LOOP;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
""",
    """CREATE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'tag_definitions' THEN 'tag'
        WHEN 'tag_aliases' THEN 'tag'
        WHEN 'creator_aliases' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'tag_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.tag_id ELSE NEW.tag_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'tag_aliases' THEN
        target_action := 'delete';
    END IF;

    PERFORM public.upsert_search_projection_request(
        'metadata', target_kind || ':' || target_id::text, target_action
    );

    -- Builds show the names of their tags, creators, and versions, so their documents are stale too.
    IF TG_TABLE_NAME IN ('tag_definitions', 'tag_aliases') THEN
        PERFORM
            public.upsert_search_projection_request('build', assignment.build_id::text, 'upsert'),
            public.upsert_discord_sync_request('build', assignment.build_id::text, 'refresh')
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        PERFORM public.upsert_search_projection_request('build', bc.build_id::text, 'upsert')
        FROM public.build_creators bc
        WHERE bc.alias_id = target_id;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        PERFORM public.upsert_search_projection_request('build', bv.build_id::text, 'upsert')
        FROM public.build_versions bv
        WHERE bv.version_id = target_id;
    END IF;
    RETURN NULL;
END;
$$;
""",
]
_FUNCTIONS = [
    """CREATE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb
    LANGUAGE sql STABLE
    SET timezone = 'UTC'
    AS $$
SELECT jsonb_build_object(
    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),
    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),
    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type, 'extension_time', d.extension_time, 'retraction_time', d.retraction_time) FROM public.extenders d WHERE d.build_id = b.id),
    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_credits c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),
    'credits', COALESCE((SELECT jsonb_agg(jsonb_build_object('kind', c.kind, 'name', c.name, 'username', c.username, 'subject', c.subject, 'role', c.role, 'attribution', c.attribution, 'source_message_id', c.source_message_id, 'alias_id', c.alias_id) ORDER BY c.position) FROM public.build_credits c WHERE c.build_id = b.id), '[]'::jsonb),
    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),
    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value::text, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'provenance', r.provenance, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),
    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),
    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),
    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)
) FROM public.builds b WHERE b.id = target_build_id;
$$;
""",
    """CREATE FUNCTION public.capture_build_edit_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    old_value jsonb;
    new_value jsonb;
    ignored text[] := ARRAY[]::text[];
    build_ids bigint[];
    target bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'Use DELETE inside a build editing scope instead of TRUNCATE' USING ERRCODE = '55000';
    END IF;
    old_value := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END;
    new_value := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END;
    IF TG_TABLE_NAME = 'builds' THEN
        ignored := ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at', 'revision', 'edited_time'];
    ELSIF TG_TABLE_NAME = 'build_tag_assignments' THEN
        ignored := ARRAY['created_at', 'updated_at'];
    ELSIF TG_TABLE_NAME = 'build_schematics' THEN
        -- The history contract retains attachment/publication metadata, not recomputable analyses.
        SELECT jsonb_object_agg(key, value) INTO old_value FROM jsonb_each(old_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
        SELECT jsonb_object_agg(key, value) INTO new_value FROM jsonb_each(new_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        ignored := ARRAY['normalized_name','claimed_at','claim_method','created_at'];
    END IF;
    IF (old_value - ignored) IS NOT DISTINCT FROM (new_value - ignored) THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_TABLE_NAME = 'creator_aliases' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_credits
        WHERE alias_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'versions' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_versions
        WHERE version_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'builds' THEN
        build_ids := ARRAY[(old_value->>'id')::bigint, (new_value->>'id')::bigint];
    ELSE
        build_ids := ARRAY[(old_value->>'build_id')::bigint, (new_value->>'build_id')::bigint];
    END IF;
    FOR target IN SELECT DISTINCT value FROM unnest(build_ids) AS value WHERE value IS NOT NULL ORDER BY value LOOP
        PERFORM public.capture_build_edit(target);
    END LOOP;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;
""",
    """CREATE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'tag_definitions' THEN 'tag'
        WHEN 'tag_aliases' THEN 'tag'
        WHEN 'creator_aliases' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'tag_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.tag_id ELSE NEW.tag_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'tag_aliases' THEN
        target_action := 'delete';
    END IF;

    PERFORM public.upsert_search_projection_request(
        'metadata', target_kind || ':' || target_id::text, target_action
    );

    -- Builds show the names of their tags, creators, and versions, so their documents are stale too.
    IF TG_TABLE_NAME IN ('tag_definitions', 'tag_aliases') THEN
        PERFORM
            public.upsert_search_projection_request('build', assignment.build_id::text, 'upsert'),
            public.upsert_discord_sync_request('build', assignment.build_id::text, 'refresh')
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        PERFORM public.upsert_search_projection_request('build', bc.build_id::text, 'upsert')
        FROM public.build_credits bc
        WHERE bc.alias_id = target_id;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        PERFORM public.upsert_search_projection_request('build', bv.build_id::text, 'upsert')
        FROM public.build_versions bv
        WHERE bv.version_id = target_id;
    END IF;
    RETURN NULL;
END;
$$;
""",
]
_ACCOUNT_FUNCTION = """CREATE FUNCTION public.build_credit_accounts(target_build_id bigint DEFAULT NULL)
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
$$;
"""
_OLD_TRIGGERS = [
    """CREATE TRIGGER build_creators_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_creators
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
""",
    """CREATE TRIGGER build_creators_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_creators
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
""",
    """CREATE TRIGGER build_creators_reject_truncate
    BEFORE TRUNCATE ON public.build_creators
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
""",
]
_TRIGGERS = [
    """CREATE TRIGGER build_credits_capture_edit
    BEFORE INSERT OR UPDATE OR DELETE ON public.build_credits
    FOR EACH ROW
    EXECUTE FUNCTION public.capture_build_edit_trigger();
""",
    """CREATE TRIGGER build_credits_enqueue_build_change
    AFTER INSERT OR DELETE OR UPDATE ON public.build_credits
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_build_change();
""",
    """CREATE TRIGGER build_credits_reject_truncate
    BEFORE TRUNCATE ON public.build_credits
    FOR EACH STATEMENT
    EXECUTE FUNCTION public.capture_build_edit_trigger();
""",
]


def upgrade() -> None:
    op.create_table(
        "build_credits",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("alias_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("build_id", "position", name="build_credits_pkey"),
        sa.ForeignKeyConstraint(["build_id"], ["builds.id"], name="build_credits_build_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["creator_aliases.id"], name="build_credits_alias_id_fkey", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["messages.id"], name="build_credits_source_message_id_fkey", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("position >= 0", name="build_credits_position_nonnegative"),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 255", name="build_credits_name_valid"
        ),
        sa.CheckConstraint(
            "kind IN ('name','discord','minecraft_java','minecraft_bedrock')", name="build_credits_kind_valid"
        ),
        sa.CheckConstraint("role IN ('creator','designer','layout','contributor')", name="build_credits_role_valid"),
        sa.CheckConstraint(
            "attribution IN ('explicit','assumed_sender','confirmed','legacy')", name="build_credits_attribution_valid"
        ),
        sa.CheckConstraint(
            "kind <> 'name' OR (subject IS NULL AND username IS NULL)", name="build_credits_generic_reference"
        ),
        comment="A build contribution and the observed identity used to attribute it.",
    )
    op.create_index("build_credits_alias_idx", "build_credits", ["alias_id"])
    op.create_index("build_credits_subject_idx", "build_credits", ["kind", "subject"])
    # Backfill before installing audit triggers: this changes representation, not attribution.
    op.execute("""
        INSERT INTO build_credits(build_id, position, alias_id, name, kind, role, attribution)
        SELECT c.build_id, row_number() OVER (PARTITION BY c.build_id ORDER BY c.alias_id) - 1,
               c.alias_id, a.name, 'name', 'creator', 'legacy'
        FROM build_creators c JOIN creator_aliases a ON a.id=c.alias_id
    """)
    for sql in _FUNCTIONS:
        op.replace_entity(PGFunction.from_sql(sql))
    op.create_entity(PGFunction.from_sql(_ACCOUNT_FUNCTION))
    op.drop_table("build_creators")
    for sql in _TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))


def downgrade() -> None:
    # A downgrade must not silently discard typed attribution or contribution roles.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("""
        SELECT EXISTS (SELECT 1 FROM build_credits c LEFT JOIN creator_aliases a ON a.id=c.alias_id
          WHERE c.alias_id IS NULL OR c.kind <> 'name' OR c.role <> 'creator'
             OR c.attribution <> 'legacy' OR c.source_message_id IS NOT NULL OR c.name <> a.name)
    """)
    ):
        msg = "Typed credits cannot be represented by the old build_creators table."
        raise RuntimeError(msg)
    op.create_table(
        "build_creators",
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("alias_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("build_id", "alias_id", name="build_creators_pkey"),
        sa.ForeignKeyConstraint(["build_id"], ["builds.id"], name="build_creators_build_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["creator_aliases.id"], name="build_creators_alias_id_fkey", ondelete="RESTRICT"
        ),
        comment="Association table between builds and the creator names credited on them.",
    )
    op.create_index("build_creators_alias_idx", "build_creators", ["alias_id"])
    op.execute("INSERT INTO build_creators(build_id, alias_id) SELECT DISTINCT build_id, alias_id FROM build_credits")
    op.drop_entity(PGFunction.from_sql(_ACCOUNT_FUNCTION))
    for sql in _OLD_FUNCTIONS:
        op.replace_entity(PGFunction.from_sql(sql))
    op.drop_table("build_credits")
    for sql in _OLD_TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))
