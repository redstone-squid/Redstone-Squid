CREATE TABLE public.title_definitions (
    id bigint NOT NULL,
    rules_revision_id bigint NOT NULL,
    materialization_source text DEFAULT 'eager'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    title text NOT NULL,
    subtitle text,
    title_diagnostics jsonb DEFAULT '[]'::jsonb NOT NULL,
    title_id uuid NOT NULL,
    CONSTRAINT title_definitions_materialization_source_check CHECK ((materialization_source = ANY (ARRAY['eager'::text, 'seeded'::text, 'public_lookup'::text])))
);

COMMENT ON TABLE public.title_definitions IS 'The wording of one stable record title under a rules revision.';

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_identity_key UNIQUE (rules_revision_id, title_id);

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_pkey PRIMARY KEY (id);

CREATE INDEX title_definitions_title_idx ON public.title_definitions USING btree (title_id);

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_rules_revision_id_fkey FOREIGN KEY (rules_revision_id) REFERENCES public.rules_revisions(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.title_definitions
    ADD CONSTRAINT title_definitions_title_id_fkey FOREIGN KEY (title_id) REFERENCES public.record_titles(public_id) ON DELETE RESTRICT;

