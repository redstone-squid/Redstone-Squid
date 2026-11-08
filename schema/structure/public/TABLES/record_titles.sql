CREATE TABLE public.record_titles (
    public_id uuid DEFAULT gen_random_uuid() NOT NULL,
    base_title text NOT NULL,
    build_kind text NOT NULL,
    version_scope text NOT NULL,
    version_id smallint,
    category_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT record_titles_base_title_check CHECK ((base_title = ANY (ARRAY['first'::text, 'fastest'::text, 'smallest'::text, 'fastest_smallest'::text, 'smallest_fastest'::text]))),
    CONSTRAINT record_titles_build_kind_check CHECK ((build_kind = ANY (ARRAY['door'::text, 'entrance'::text, 'extender'::text, 'utility'::text]))),
    CONSTRAINT record_titles_version_scope_check CHECK ((version_scope = ANY (ARRAY['all_time'::text, 'up_to_date'::text])))
);

COMMENT ON TABLE public.record_titles IS 'A stable public identity for a record title across rules revisions.';

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_identity_key UNIQUE NULLS NOT DISTINCT (base_title, build_kind, version_scope, version_id, category_key);

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_pkey PRIMARY KEY (public_id);

CREATE INDEX record_titles_category_idx ON public.record_titles USING btree (build_kind, base_title, category_key);

ALTER TABLE ONLY public.record_titles
    ADD CONSTRAINT record_titles_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.versions(id) ON DELETE RESTRICT;

