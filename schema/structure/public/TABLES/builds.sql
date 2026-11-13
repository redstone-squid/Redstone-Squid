CREATE TABLE public.builds (
    id bigint NOT NULL,
    submission_status smallint NOT NULL,
    edited_time timestamp with time zone DEFAULT now() NOT NULL,
    extra_info jsonb DEFAULT '{}'::jsonb NOT NULL,
    width integer,
    height integer,
    depth integer,
    completion_time text,
    submission_time timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    category text NOT NULL,
    ai_generated boolean NOT NULL,
    version_spec text,
    embedding public.vector(1536),
    locked_at timestamp with time zone,
    completion_at timestamp with time zone,
    completion_evidence text,
    description text,
    submitter_account_id integer NOT NULL,
    lock_token uuid,
    lock_expires_at timestamp with time zone,
    revision bigint DEFAULT 1 NOT NULL,
    display_name text,
    source_submission_draft_id uuid,
    sponsor_installation_id uuid,
    sponsor_display_name text,
    sponsor_address text,
    sponsor_description text,
    sponsor_website_url text,
    CONSTRAINT builds_category_check CHECK ((category = ANY (ARRAY['Door'::text, 'Extender'::text, 'Utility'::text, 'Entrance'::text, 'Other'::text]))),
    CONSTRAINT builds_display_name_valid CHECK (((display_name IS NULL) OR ((display_name = btrim(display_name)) AND (display_name <> ''::text) AND (char_length(display_name) <= 120)))),
    CONSTRAINT builds_lock_lease_complete CHECK ((((locked_at IS NULL) AND (lock_token IS NULL) AND (lock_expires_at IS NULL)) OR ((locked_at IS NOT NULL) AND (lock_token IS NOT NULL) AND (lock_expires_at IS NOT NULL)))),
    CONSTRAINT builds_revision_positive CHECK ((revision > 0)),
    CONSTRAINT builds_sponsor_address_length CHECK (((sponsor_address IS NULL) OR ((char_length(sponsor_address) >= 1) AND (char_length(sponsor_address) <= 255)))),
    CONSTRAINT builds_sponsor_description_length CHECK (((sponsor_description IS NULL) OR ((char_length(sponsor_description) >= 1) AND (char_length(sponsor_description) <= 500)))),
    CONSTRAINT builds_sponsor_display_name_length CHECK (((sponsor_display_name IS NULL) OR ((char_length(sponsor_display_name) >= 1) AND (char_length(sponsor_display_name) <= 80)))),
    CONSTRAINT builds_sponsor_projection_complete CHECK (((sponsor_installation_id IS NOT NULL) OR ((sponsor_display_name IS NULL) AND (sponsor_address IS NULL) AND (sponsor_description IS NULL) AND (sponsor_website_url IS NULL)))),
    CONSTRAINT builds_sponsor_website_valid CHECK (((sponsor_website_url IS NULL) OR (((char_length(sponsor_website_url) >= 1) AND (char_length(sponsor_website_url) <= 2048)) AND (sponsor_website_url ~ '^https?://'::text)))),
    CONSTRAINT check_status CHECK ((submission_status = ANY (ARRAY[0, 1, 2]))),
    CONSTRAINT submissions_build_depth_check CHECK ((depth > 0)),
    CONSTRAINT submissions_build_height_check CHECK ((height > 0)),
    CONSTRAINT submissions_build_width_check CHECK ((width > 0))
);

COMMENT ON TABLE public.builds IS 'A build submitted by a user.';

COMMENT ON COLUMN public.builds.width IS 'Declared width in blocks; None means unknown.';

COMMENT ON COLUMN public.builds.completion_time IS 'Completion date as supplied by the submitter, including partial dates.';

COMMENT ON COLUMN public.builds.embedding IS 'Application-owned semantic vector stored in the authoritative build row.';

COMMENT ON COLUMN public.builds.revision IS 'Optimistic version checked by explicit repository writes.';

COMMENT ON COLUMN public.builds.source_submission_draft_id IS 'Stable finalization key retained even if the short-lived source draft is later pruned.';

COMMENT ON COLUMN public.builds.sponsor_installation_id IS 'Opaque Paper installation ID snapshotted without a mutable foreign key.';

ALTER TABLE ONLY public.builds ALTER COLUMN id SET DEFAULT nextval('public.submissions_submission_id_seq'::regclass);

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT builds_source_submission_draft_id_key UNIQUE (source_submission_draft_id);

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT submissions_pkey PRIMARY KEY (id);

CREATE INDEX builds_embedding_hnsw_idx ON public.builds USING hnsw (embedding public.vector_cosine_ops) WHERE (embedding IS NOT NULL);

CREATE INDEX builds_submitter_idx ON public.builds USING btree (submitter_account_id);

CREATE INDEX idx_builds_category ON public.builds USING btree (category);

CREATE INDEX idx_builds_submission_time ON public.builds USING btree (submission_time DESC);

CREATE TRIGGER builds_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE CONSTRAINT TRIGGER builds_category_subtype_check AFTER INSERT OR UPDATE OF id, category ON public.builds DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_build_category_subtype();

CREATE TRIGGER builds_emit_domain_event AFTER INSERT OR UPDATE OF submission_status ON public.builds FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();

CREATE TRIGGER builds_enqueue_build_change AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_change();

CREATE TRIGGER builds_enqueue_build_change_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN (((to_jsonb(old.*) - ARRAY['embedding'::text, 'locked_at'::text, 'lock_token'::text, 'lock_expires_at'::text]) IS DISTINCT FROM (to_jsonb(new.*) - ARRAY['embedding'::text, 'locked_at'::text, 'lock_token'::text, 'lock_expires_at'::text]))) EXECUTE FUNCTION public.enqueue_build_change();

CREATE TRIGGER builds_reject_truncate BEFORE TRUNCATE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.builds
    ADD CONSTRAINT builds_submitter_account_id_fkey FOREIGN KEY (submitter_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;

