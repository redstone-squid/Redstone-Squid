CREATE TABLE public.tag_definitions (
    id bigint NOT NULL,
    stable_key text NOT NULL,
    display_name text NOT NULL,
    normalized_name text NOT NULL,
    query_name text,
    authority text NOT NULL,
    semantic_kind text NOT NULL,
    restriction_type text,
    value_type text NOT NULL,
    record_operator text,
    canonical_unit_key text,
    default_display_unit_key text,
    numeric_step numeric,
    render_template text NOT NULL,
    default_display_order smallint NOT NULL,
    moderation_status text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    created_by_account_id integer,
    revision bigint DEFAULT 1 NOT NULL,
    withdrawn boolean DEFAULT false NOT NULL,
    CONSTRAINT tag_definitions_authority_check CHECK ((authority = ANY (ARRAY['official'::text, 'user'::text]))),
    CONSTRAINT tag_definitions_display_order_check CHECK ((default_display_order >= 0)),
    CONSTRAINT tag_definitions_moderation_status_check CHECK ((moderation_status = ANY (ARRAY['pending'::text, 'approved'::text, 'rejected'::text, 'archived'::text]))),
    CONSTRAINT tag_definitions_non_numeric_unit_check CHECK (((value_type = 'numeric'::text) OR ((canonical_unit_key IS NULL) AND (default_display_unit_key IS NULL) AND (numeric_step IS NULL)))),
    CONSTRAINT tag_definitions_numeric_step_check CHECK (((numeric_step IS NULL) OR (numeric_step > (0)::numeric))),
    CONSTRAINT tag_definitions_query_name_format_check CHECK (((query_name IS NULL) OR (query_name ~ '^[a-z][a-z0-9_]{0,63}$'::text))),
    CONSTRAINT tag_definitions_record_operator_check CHECK (((record_operator IS NULL) OR (record_operator = ANY (ARRAY['present'::text, 'exact'::text, 'at_most'::text, 'at_least'::text])))),
    CONSTRAINT tag_definitions_record_operator_value_check CHECK ((((record_operator = 'present'::text) AND (value_type = 'none'::text)) OR ((record_operator = ANY (ARRAY['at_most'::text, 'at_least'::text])) AND (value_type = 'numeric'::text)) OR ((record_operator = 'exact'::text) AND (value_type <> 'none'::text)) OR (record_operator IS NULL))),
    CONSTRAINT tag_definitions_restriction_type_check CHECK ((((semantic_kind = 'restriction'::text) AND (restriction_type IS NOT NULL)) OR ((semantic_kind <> 'restriction'::text) AND (restriction_type IS NULL)))),
    CONSTRAINT tag_definitions_revision_positive CHECK ((revision > 0)),
    CONSTRAINT tag_definitions_semantic_kind_check CHECK ((semantic_kind = ANY (ARRAY['restriction'::text, 'pattern'::text, 'showcase'::text]))),
    CONSTRAINT tag_definitions_user_showcase_only_check CHECK (((authority = 'official'::text) OR ((semantic_kind = 'showcase'::text) AND (restriction_type IS NULL) AND (record_operator IS NULL)))),
    CONSTRAINT tag_definitions_value_type_check CHECK ((value_type = ANY (ARRAY['none'::text, 'numeric'::text, 'text'::text, 'boolean'::text])))
);

COMMENT ON TABLE public.tag_definitions IS 'A canonical tag that may be assigned to builds.';

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_id_value_type_key UNIQUE (id, value_type);

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_query_name_key UNIQUE (query_name);

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_stable_key_key UNIQUE (stable_key);

CREATE INDEX tag_definitions_created_by_idx ON public.tag_definitions USING btree (created_by_account_id);

CREATE INDEX tag_definitions_lookup_idx ON public.tag_definitions USING btree (normalized_name, semantic_kind);

CREATE TRIGGER tag_definitions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.tag_definitions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_canonical_unit_fkey FOREIGN KEY (canonical_unit_key) REFERENCES public.tag_units(key) ON DELETE RESTRICT;

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.tag_definitions
    ADD CONSTRAINT tag_definitions_display_unit_fkey FOREIGN KEY (default_display_unit_key) REFERENCES public.tag_units(key) ON DELETE RESTRICT;

