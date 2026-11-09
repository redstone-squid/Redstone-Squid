"""Retain immutable build history with shared snapshots.

Pause application writers for migration and deploy history-aware writers before
resuming. Existing builds receive a baseline, not reconstructed edit history.
"""

from alembic import op

revision = "d0a5f8b3c6e9"
down_revision = "c9f4e7a2b5d8"
branch_labels = None
depends_on = None

_SQL = r"""

CREATE TABLE build_history_states (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	build_id BIGINT NOT NULL,
	captured_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	build_revision BIGINT NOT NULL,
	schema_version SMALLINT NOT NULL,
	snapshot JSONB NOT NULL,
	CONSTRAINT build_history_states_pkey PRIMARY KEY (id),
	CONSTRAINT build_history_states_build_id_key UNIQUE (build_id, id),
	CONSTRAINT build_history_states_revision_positive CHECK (build_revision > 0),
	CONSTRAINT build_history_states_schema_positive CHECK (schema_version > 0),
	CONSTRAINT build_history_states_snapshot_object CHECK (jsonb_typeof(snapshot) = 'object')
)

;
COMMENT ON TABLE build_history_states IS 'One observed build state, shared by adjacent history entries.';

CREATE TABLE build_edit_history (
	id BIGINT GENERATED ALWAYS AS IDENTITY,
	build_id BIGINT NOT NULL,
	recorded_at TIMESTAMP WITH TIME ZONE DEFAULT clock_timestamp() NOT NULL,
	operation_id UUID NOT NULL,
	operation TEXT NOT NULL,
	source TEXT NOT NULL,
	actor_account_id INTEGER,
	actor_system TEXT,
	correlation_kind TEXT,
	correlation_id TEXT,
	before_state_id BIGINT,
	after_state_id BIGINT,
	CONSTRAINT build_edit_history_pkey PRIMARY KEY (id),
	CONSTRAINT build_edit_history_build_id_key UNIQUE (build_id, operation_id),
	CONSTRAINT build_history_before_fkey FOREIGN KEY(build_id, before_state_id) REFERENCES build_history_states (build_id, id),
	CONSTRAINT build_history_after_fkey FOREIGN KEY(build_id, after_state_id) REFERENCES build_history_states (build_id, id),
	CONSTRAINT build_history_actor_check CHECK (num_nonnulls(actor_account_id, actor_system) = 1),
	CONSTRAINT build_history_account_positive CHECK (actor_account_id IS NULL OR actor_account_id > 0),
	CONSTRAINT build_history_source_check CHECK (length(btrim(source)) > 0 AND (actor_system IS NULL OR length(btrim(actor_system)) > 0)),
	CONSTRAINT build_history_correlation_check CHECK ((correlation_kind IS NULL) = (correlation_id IS NULL)),
	CONSTRAINT build_history_states_check CHECK ((operation IN ('baseline', 'create') AND before_state_id IS NULL AND after_state_id IS NOT NULL) OR (operation = 'delete' AND before_state_id IS NOT NULL AND after_state_id IS NULL) OR (operation IN ('edit', 'moderation', 'recalculation', 'schematic', 'account_merge') AND before_state_id IS NOT NULL AND after_state_id IS NOT NULL))
)

;
CREATE INDEX build_edit_history_timeline_idx ON build_edit_history (build_id, id);
COMMENT ON TABLE build_edit_history IS 'An append-only operation whose states and actor survive live-record deletion.';
CREATE FUNCTION public.build_history_snapshot_v1(target_build_id bigint) RETURNS jsonb
    LANGUAGE sql STABLE
    SET timezone = 'UTC'
    AS $$
SELECT jsonb_build_object(
    'build', jsonb_build_object('submission_status', b.submission_status, 'extra_info', b.extra_info, 'width', b.width, 'height', b.height, 'depth', b.depth, 'completion_time', b.completion_time, 'submission_time', b.submission_time, 'category', b.category, 'ai_generated', b.ai_generated, 'version_spec', b.version_spec, 'completion_at', b.completion_at, 'completion_evidence', b.completion_evidence, 'description', b.description, 'submitter_account_id', b.submitter_account_id, 'display_name', b.display_name, 'source_submission_draft_id', b.source_submission_draft_id, 'sponsor_installation_id', b.sponsor_installation_id, 'sponsor_display_name', b.sponsor_display_name, 'sponsor_address', b.sponsor_address, 'sponsor_description', b.sponsor_description, 'sponsor_website_url', b.sponsor_website_url),
    'doors', (SELECT jsonb_build_object('orientation', d.orientation, 'door_width', d.door_width, 'door_height', d.door_height, 'door_depth', d.door_depth, 'normal_opening_time', d.normal_opening_time, 'normal_closing_time', d.normal_closing_time, 'visible_opening_time', d.visible_opening_time, 'visible_closing_time', d.visible_closing_time) FROM public.doors d WHERE d.build_id = b.id),
    'extenders', (SELECT jsonb_build_object('orientation', d.orientation, 'extension_length', d.extension_length, 'extender_type', d.extender_type) FROM public.extenders d WHERE d.build_id = b.id),
    'creators', COALESCE((SELECT jsonb_agg(jsonb_build_object('alias_id', a.id, 'name', a.name, 'account_id', a.account_id) ORDER BY a.id) FROM public.build_creators c JOIN public.creator_aliases a ON a.id = c.alias_id WHERE c.build_id = b.id), '[]'::jsonb),
    'versions', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', v.id, 'edition', v.edition, 'major_version', v.major_version, 'minor_version', v.minor_version, 'patch_number', v.patch_number, 'data_version', v.data_version) ORDER BY v.id) FROM public.build_versions bv JOIN public.versions v ON v.id = bv.version_id WHERE bv.build_id = b.id), '[]'::jsonb),
    'tags', COALESCE((SELECT jsonb_agg(jsonb_build_object('tag_id', r.tag_id, 'value_type', r.value_type, 'numeric_value', r.numeric_value, 'text_value', r.text_value, 'boolean_value', r.boolean_value, 'display_unit_key', r.display_unit_key, 'display_order', r.display_order, 'evidence', r.evidence, 'provenance', r.provenance, 'created_by_account_id', r.created_by_account_id) ORDER BY r.tag_id) FROM public.build_tag_assignments r  WHERE r.build_id = b.id), '[]'::jsonb),
    'links', COALESCE((SELECT jsonb_agg(jsonb_build_object('url', r.url, 'media_type', r.media_type) ORDER BY r.media_type, r.url) FROM public.build_links r  WHERE r.build_id = b.id), '[]'::jsonb),
    'source_messages', COALESCE((SELECT jsonb_agg(jsonb_build_object('message_id', r.message_id) ORDER BY r.position, r.message_id) FROM public.build_source_messages r  WHERE r.build_id = b.id), '[]'::jsonb),
    'schematics', COALESCE((SELECT jsonb_agg(jsonb_build_object('id', r.id, 'file_sha256', r.file_sha256, 'is_primary', r.is_primary, 'original_filename', r.original_filename, 'uploaded_by_account_id', r.uploaded_by_account_id, 'visibility', r.visibility, 'license_code', r.license_code, 'rights_attested_at', r.rights_attested_at, 'rights_attested_by_account_id', r.rights_attested_by_account_id, 'published_at', r.published_at, 'withdrawn_at', r.withdrawn_at) ORDER BY r.id) FROM public.build_schematics r  WHERE r.build_id = b.id), '[]'::jsonb)
) FROM public.builds b WHERE b.id = target_build_id;
$$;

CREATE FUNCTION public.reject_build_history_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'build history is append-only' USING ERRCODE = '55000';
END;
$$;

CREATE TRIGGER build_history_states_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.build_history_states FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();

CREATE TRIGGER build_edit_history_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.build_edit_history FOR EACH STATEMENT EXECUTE FUNCTION public.reject_build_history_mutation();


INSERT INTO build_history_states (build_id, build_revision, schema_version, snapshot)
SELECT id, revision, 1, public.build_history_snapshot_v1(id) FROM builds ORDER BY id;
INSERT INTO build_edit_history (build_id, operation_id, operation, source, actor_system, after_state_id)
SELECT build_id, gen_random_uuid(), 'baseline', 'migration', 'history_baseline', id
FROM build_history_states ORDER BY build_id;

"""


def upgrade() -> None:
    op.execute(_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE build_edit_history; DROP TABLE build_history_states;")
    op.execute("DROP FUNCTION public.reject_build_history_mutation();")
    op.execute("DROP FUNCTION public.build_history_snapshot_v1(bigint);")
