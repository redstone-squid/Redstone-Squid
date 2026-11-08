CREATE TABLE public.submission_draft_schematics (
    draft_id uuid NOT NULL,
    source_id uuid NOT NULL,
    is_primary boolean DEFAULT false NOT NULL,
    discarded boolean DEFAULT false NOT NULL,
    CONSTRAINT submission_draft_schematics_discarded_primary_check CHECK (((NOT discarded) OR (NOT is_primary)))
);

COMMENT ON TABLE public.submission_draft_schematics IS 'One draft''s retained schematic reference and explicit primary selection.';

ALTER TABLE ONLY public.submission_draft_schematics
    ADD CONSTRAINT submission_draft_schematics_pkey PRIMARY KEY (draft_id, source_id);

CREATE UNIQUE INDEX submission_draft_schematics_one_primary ON public.submission_draft_schematics USING btree (draft_id) WHERE is_primary;

CREATE INDEX submission_draft_schematics_source_idx ON public.submission_draft_schematics USING btree (source_id);

ALTER TABLE ONLY public.submission_draft_schematics
    ADD CONSTRAINT submission_draft_schematics_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.submission_drafts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.submission_draft_schematics
    ADD CONSTRAINT submission_draft_schematics_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.submission_schematic_sources(id) ON DELETE RESTRICT;

