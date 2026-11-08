CREATE TABLE public.build_tag_assignments (
    build_id bigint NOT NULL,
    tag_id bigint NOT NULL,
    value_type text NOT NULL,
    numeric_value numeric,
    text_value text,
    boolean_value boolean,
    display_unit_key text,
    display_order smallint,
    evidence text,
    provenance text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_account_id integer,
    CONSTRAINT build_tag_assignments_finite_numeric_check CHECK (((numeric_value IS NULL) OR ((numeric_value)::text <> ALL (ARRAY['NaN'::text, 'Infinity'::text, '-Infinity'::text])))),
    CONSTRAINT build_tag_assignments_order_check CHECK (((display_order IS NULL) OR (display_order >= 0))),
    CONSTRAINT build_tag_assignments_provenance_check CHECK ((provenance = ANY (ARRAY['submitted'::text, 'inferred'::text, 'moderated'::text, 'legacy_import'::text]))),
    CONSTRAINT build_tag_assignments_typed_value_check CHECK ((((value_type = 'none'::text) AND (num_nonnulls(numeric_value, text_value, boolean_value) = 0)) OR ((value_type = 'numeric'::text) AND (numeric_value IS NOT NULL) AND (num_nonnulls(text_value, boolean_value) = 0)) OR ((value_type = 'text'::text) AND (text_value IS NOT NULL) AND (num_nonnulls(numeric_value, boolean_value) = 0)) OR ((value_type = 'boolean'::text) AND (boolean_value IS NOT NULL) AND (num_nonnulls(numeric_value, text_value) = 0))))
);

COMMENT ON TABLE public.build_tag_assignments IS 'A typed tag value attached to one build.';

COMMENT ON COLUMN public.build_tag_assignments.display_unit_key IS 'Unit used for presentation; the stored numeric value uses the definition''s canonical unit.';

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_pkey PRIMARY KEY (build_id, tag_id);

CREATE INDEX build_tag_assignments_created_by_idx ON public.build_tag_assignments USING btree (created_by_account_id);

CREATE INDEX build_tag_assignments_numeric_idx ON public.build_tag_assignments USING btree (tag_id, numeric_value, build_id) WHERE (numeric_value IS NOT NULL);

CREATE INDEX build_tag_assignments_tag_build_idx ON public.build_tag_assignments USING btree (tag_id, build_id);

CREATE INDEX build_tag_assignments_text_idx ON public.build_tag_assignments USING btree (tag_id, text_value, build_id) WHERE (text_value IS NOT NULL);

CREATE TRIGGER build_tag_assignments_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

CREATE TRIGGER build_tag_assignments_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_tag_assignments FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_created_by_account_id_fkey FOREIGN KEY (created_by_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_definition_value_fkey FOREIGN KEY (tag_id, value_type) REFERENCES public.tag_definitions(id, value_type) ON DELETE RESTRICT;

ALTER TABLE ONLY public.build_tag_assignments
    ADD CONSTRAINT build_tag_assignments_display_unit_fkey FOREIGN KEY (display_unit_key) REFERENCES public.tag_units(key) ON DELETE RESTRICT;

