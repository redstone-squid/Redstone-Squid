CREATE TABLE public.record_titles (
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    base_title text NOT NULL,
    build_kind text NOT NULL,
    version_scope text NOT NULL,
    version_id smallint,
    category_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    input_generation bigint,
    retired_at timestamp with time zone,
    CONSTRAINT record_titles_base_title_check CHECK ((base_title = ANY (ARRAY['first'::text, 'fastest'::text, 'smallest'::text, 'fastest_smallest'::text, 'smallest_fastest'::text]))),
    CONSTRAINT record_titles_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_titles_version_scope_check CHECK ((version_scope = ANY (ARRAY['all_time'::text, 'up_to_date'::text])))
);

COMMENT ON TABLE public.record_titles IS 'A stable public identity for a record title across rules revisions.';

COMMENT ON COLUMN public.record_titles.input_generation IS 'The kind''s input generation last published or retired here; older batches leave the record alone.';

COMMENT ON COLUMN public.record_titles.retired_at IS 'When the record last lost its competitors; null while it has a result.';

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_identity_key UNIQUE NULLS NOT DISTINCT (base_title, build_kind, version_scope, version_id, category_key);

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_pkey PRIMARY KEY (public_id);

CREATE INDEX record_titles_category_idx ON public.record_titles USING btree (build_kind, base_title, category_key);

CREATE TRIGGER record_titles_enqueue_search_delete AFTER DELETE ON public.record_titles REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

CREATE TRIGGER record_titles_enqueue_search_update AFTER UPDATE ON public.record_titles REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION public.enqueue_record_search_projection();

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;

